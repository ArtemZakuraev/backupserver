package api

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"

	"backup-server-agent/internal/backup"
	"backup-server-agent/internal/config"
	"backup-server-agent/internal/cron"
	"backup-server-agent/internal/logger"
	"backup-server-agent/internal/monitor"
	"github.com/gin-gonic/gin"
)

type Router struct {
	config     *config.Config
	logger     *logger.Logger
	cronManager *cron.CronManager
}

func NewRouter(cfg *config.Config, log *logger.Logger, cronMgr *cron.CronManager) *gin.Engine {
	router := &Router{
		config:      cfg,
		logger:      log,
		cronManager: cronMgr,
	}

	r := gin.Default()

	// Middleware для проверки IP и hostname сервера
	r.Use(router.authMiddleware)

	// Ping endpoint
	r.GET("/ping", router.ping)

	// API endpoints
	api := r.Group("/api")
	{
		api.GET("/system", router.getSystemInfo)
		api.GET("/disks", router.getAllDisks)
		api.POST("/filesystem", router.getFilesystemInfo)
		api.POST("/task/config", router.setTaskConfig)
		api.POST("/task/execute", router.executeTask)
		api.GET("/backups", router.getBackups)
		
		// PostgreSQL endpoints
		api.POST("/postgres/connection", router.setPostgresConnection)
		api.DELETE("/postgres/connection/:id", router.deletePostgresConnection)
		api.GET("/postgres/connections", router.getPostgresConnections)
		api.POST("/postgres/task/config", router.setPostgresTaskConfig)
		api.DELETE("/postgres/task/:id", router.deletePostgresTask)
		api.POST("/postgres/backup", router.executePostgresBackup)
		api.POST("/postgres/restore", router.executePostgresRestore)
		
		// Storage endpoints
		api.POST("/storage/upload", router.uploadFile)
		api.POST("/storage/space", router.getStorageSpace)
	}

	return r
}

func (r *Router) authMiddleware(c *gin.Context) {
	// Для ping endpoint не требуем авторизацию
	if c.Request.URL.Path == "/ping" {
		c.Next()
		return
	}

	clientIP := c.ClientIP()
	hostname := c.GetHeader("X-Hostname")

	// Проверяем IP
	if r.config.ServerIP != "" && clientIP != r.config.ServerIP {
		c.JSON(http.StatusForbidden, gin.H{"error": "Forbidden"})
		c.Abort()
		return
	}

	// Проверяем hostname, если указан
	if r.config.ServerHostname != "" && hostname != r.config.ServerHostname {
		c.JSON(http.StatusForbidden, gin.H{"error": "Forbidden"})
		c.Abort()
		return
	}

	c.Next()
}

func (r *Router) ping(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (r *Router) getSystemInfo(c *gin.Context) {
	info, err := monitor.GetSystemInfo()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, info)
}

func (r *Router) getAllDisks(c *gin.Context) {
	disks, err := monitor.GetAllDisks()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"disks": disks})
}

func (r *Router) getFilesystemInfo(c *gin.Context) {
	var req struct {
		Path string `json:"path"`
	}

	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	filesystem, mountPoint, total, available, err := monitor.GetFilesystemInfo(req.Path)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"filesystem":  filesystem,
		"mount_point": mountPoint,
		"total_gb":    total,
		"available_gb": available,
	})
}

func (r *Router) setTaskConfig(c *gin.Context) {
	var task config.Task
	if err := c.ShouldBindJSON(&task); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Обновляем конфигурацию
	r.config.AddOrUpdateTask(task)

	// Сохраняем конфигурацию
	if err := config.Save(r.config); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	// Обновляем cron
	r.cronManager.AddTask(task)

	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (r *Router) executeTask(c *gin.Context) {
	var req struct {
		TaskID          int    `json:"task_id"`
		SourcePath      string `json:"source_path"`
		CreateArchive   bool   `json:"create_archive"`
		ArchiveFormat   string `json:"archive_format"`
		S3Endpoint      string `json:"s3_endpoint"`
		S3AccessKey     string `json:"s3_access_key"`
		S3SecretKey     string `json:"s3_secret_key"`
		S3Bucket        string `json:"s3_bucket"`
		S3Region        string `json:"s3_region"`
		StorageType     string `json:"storage_type"`
		StorageConfig   string `json:"storage_config"`
		CleanupEnabled  bool   `json:"cleanup_enabled"`
		CleanupDays     int    `json:"cleanup_days"`
		IsDockerCompose bool   `json:"is_docker_compose"`
		DockerComposePath string `json:"docker_compose_path"`
	}

	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Создаем задачу из запроса
	task := config.Task{
		TaskID:           req.TaskID,
		SourcePath:       req.SourcePath,
		CreateArchive:    req.CreateArchive,
		ArchiveFormat:    req.ArchiveFormat,
		S3Endpoint:       req.S3Endpoint,
		S3AccessKey:      req.S3AccessKey,
		S3SecretKey:      req.S3SecretKey,
		S3Bucket:         req.S3Bucket,
		S3Region:         req.S3Region,
		StorageType:      req.StorageType,
		StorageConfig:    req.StorageConfig,
		CleanupEnabled:   req.CleanupEnabled,
		CleanupDays:      req.CleanupDays,
		IsDockerCompose:  req.IsDockerCompose,
		DockerComposePath: req.DockerComposePath,
	}

	// Получаем IP сервера из конфига
	serverIP := r.config.ServerIP
	if serverIP == "" {
		serverIP = "unknown"
	}

	// Выполняем бэкап
	result, err := backup.ExecuteBackup(task, serverIP, r.logger)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

		c.JSON(http.StatusOK, gin.H{
			"success":      result.Success,
			"archive_size": result.ArchiveSize,
			"files_count":  result.FilesCount,
			"s3_path":      result.S3Path,
		})
}

func (r *Router) getBackups(c *gin.Context) {
	records, err := backup.GetBackupRecords()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	// Конвертируем в формат для API
	backups := make([]map[string]interface{}, len(records))
	for i, record := range records {
		backupMap := map[string]interface{}{
			"source_path":    record.SourcePath,
			"archive_name":    record.ArchiveName,
			"backup_date":     record.BackupDate,
			"archive_size_mb": record.ArchiveSizeMB,
			"status":          record.Status,
		}
		if record.S3UploadDate != nil {
			backupMap["s3_upload_date"] = *record.S3UploadDate
		}
		if record.S3Path != "" {
			backupMap["s3_path"] = record.S3Path
		}
		backups[i] = backupMap
	}

	c.JSON(http.StatusOK, gin.H{"backups": backups})
}

// PostgreSQL connection endpoints

func (r *Router) setPostgresConnection(c *gin.Context) {
	var conn config.PostgresConnection
	if err := c.ShouldBindJSON(&conn); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Обновляем конфигурацию
	r.config.AddOrUpdatePostgresConnection(conn)

	// Сохраняем конфигурацию
	if err := config.Save(r.config); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (r *Router) deletePostgresConnection(c *gin.Context) {
	connID := c.Param("id")
	if connID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "connection id required"})
		return
	}

	var id int
	if _, err := fmt.Sscanf(connID, "%d", &id); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid connection id"})
		return
	}

	// Удаляем подключение
	r.config.RemovePostgresConnection(id)

	// Сохраняем конфигурацию
	if err := config.Save(r.config); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (r *Router) getPostgresConnections(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"connections": r.config.PostgresConnections})
}

// PostgreSQL task endpoints

func (r *Router) setPostgresTaskConfig(c *gin.Context) {
	var task config.PostgresTask
	if err := c.ShouldBindJSON(&task); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Обновляем задачу
	config.AddOrUpdatePostgresTask(task)

	// Обновляем cron
	r.cronManager.AddPostgresTask(task)

	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (r *Router) deletePostgresTask(c *gin.Context) {
	taskID := c.Param("id")
	if taskID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "task id required"})
		return
	}

	var id int
	if _, err := fmt.Sscanf(taskID, "%d", &id); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid task id"})
		return
	}

	// Удаляем задачу
	config.RemovePostgresTask(id)

	// Удаляем из cron
	r.cronManager.RemovePostgresTask(id)

	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

func (r *Router) executePostgresBackup(c *gin.Context) {
	var req struct {
		TaskID       int `json:"task_id"`
		ConnectionID int `json:"connection_id"`
	}

	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Получаем задачу
	task := config.GetPostgresTask(req.TaskID)
	if task == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "task not found"})
		return
	}

	// Получаем подключение
	conn := r.config.GetPostgresConnection(req.ConnectionID)
	if conn == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "connection not found"})
		return
	}

	// Получаем IP сервера из конфига
	serverIP := r.config.ServerIP
	if serverIP == "" {
		serverIP = "unknown"
	}

	// Выполняем бэкап
	result, err := backup.ExecutePostgresBackup(*task, *conn, serverIP, r.logger)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success":      result.Success,
		"dump_size_mb": result.DumpSizeMB,
		"dump_filename": result.DumpFilename,
		"storage_path": result.StoragePath,
		"error":        result.Error,
	})
}

func (r *Router) executePostgresRestore(c *gin.Context) {
	var req struct {
		TaskID         int    `json:"task_id"`
		ConnectionID   int    `json:"connection_id"`
		StoragePath    string `json:"storage_path"`
		TargetDatabase string `json:"target_database,omitempty"`
	}

	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Получаем задачу
	task := config.GetPostgresTask(req.TaskID)
	if task == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "task not found"})
		return
	}

	// Получаем подключение
	conn := r.config.GetPostgresConnection(req.ConnectionID)
	if conn == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "connection not found"})
		return
	}

	// Выполняем восстановление
	result, err := backup.RestorePostgresBackup(*task, *conn, req.StoragePath, req.TargetDatabase, r.logger)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{
			"success": false,
			"error":   err.Error(),
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"success": result.Success,
		"error":   result.Error,
	})
}

// Storage upload endpoint
func (r *Router) uploadFile(c *gin.Context) {
	// Получаем параметры из query string
	basePath := c.Query("base_path")
	if basePath == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "base_path parameter is required"})
		return
	}

	// Получаем файл из multipart form
	file, err := c.FormFile("file")
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "file is required: " + err.Error()})
		return
	}	// Создаем директорию если её нет
	if err := os.MkdirAll(basePath, 0755); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to create directory: " + err.Error()})
		return
	}

	// Формируем полный путь к файлу
	fullPath := filepath.Join(basePath, file.Filename)

	// Сохраняем файл
	if err := c.SaveUploadedFile(file, fullPath); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to save file: " + err.Error()})
		return
	}

	r.logger.Infof("File uploaded successfully: %s (size: %d bytes)", fullPath, file.Size)

	c.JSON(http.StatusOK, gin.H{
		"success": true,
		"path":    fullPath,
		"size":    file.Size,
	})
}

// Storage space endpoint
func (r *Router) getStorageSpace(c *gin.Context) {
	var req struct {
		Path string `json:"path"`
	}

	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	if req.Path == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "path parameter is required"})
		return
	}

	// Получаем информацию о месте на диске
	spaceInfo, err := monitor.GetStorageSpace(req.Path)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to get storage space: " + err.Error()})
		return
	}

	c.JSON(http.StatusOK, spaceInfo)
}
