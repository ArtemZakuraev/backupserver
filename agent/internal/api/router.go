package api

import (
	"net/http"

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

