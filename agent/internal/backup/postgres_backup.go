package backup

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"backup-server-agent/internal/config"
	"backup-server-agent/internal/logger"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

type PostgresBackupResult struct {
	Success      bool
	DumpPath     string
	DumpSizeMB   float64
	DumpFilename string
	StoragePath  string
	Error        string
}

// ExecutePostgresBackup выполняет резервное копирование PostgreSQL базы данных
func ExecutePostgresBackup(task config.PostgresTask, conn config.PostgresConnection, serverIP string, log *logger.Logger) (*PostgresBackupResult, error) {
	result := &PostgresBackupResult{}

	// Расшифровываем пароль (в будущем можно добавить шифрование)
	password := conn.Password

	// Формируем имя файла дампа
	timestamp := time.Now().Format("20060102_150405")
	dbNameSafe := strings.ReplaceAll(strings.ReplaceAll(task.Database, "/", "_"), "\\", "_")

	var dumpFilename string
	switch task.BackupFormat {
	case "custom":
		dumpFilename = fmt.Sprintf("%s_%s.dump", dbNameSafe, timestamp)
	case "plain":
		dumpFilename = fmt.Sprintf("%s_%s.sql", dbNameSafe, timestamp)
	case "tar":
		dumpFilename = fmt.Sprintf("%s_%s.tar", dbNameSafe, timestamp)
	default:
		dumpFilename = fmt.Sprintf("%s_%s.sql", dbNameSafe, timestamp)
	}

	// Создаем временную директорию для дампов
	tempDir := "/tmp/postgres_backups"
	if err := os.MkdirAll(tempDir, 0755); err != nil {
		result.Error = fmt.Sprintf("Failed to create temp directory: %v", err)
		return result, fmt.Errorf(result.Error)
	}

	dumpPath := filepath.Join(tempDir, dumpFilename)

	// Формируем команду pg_dump
	cmd := exec.Command(
		"pg_dump",
		fmt.Sprintf("--host=%s", conn.Host),
		fmt.Sprintf("--port=%d", conn.Port),
		fmt.Sprintf("--username=%s", conn.Username),
		fmt.Sprintf("--dbname=%s", task.Database),
		fmt.Sprintf("--format=%s", task.BackupFormat),
		fmt.Sprintf("--file=%s", dumpPath),
	)

	// Добавляем опции в зависимости от формата
	if task.BackupFormat == "custom" {
		cmd.Args = append(cmd.Args, fmt.Sprintf("--compress=%d", task.CompressionLevel))
	} else if task.BackupFormat == "plain" {
		cmd.Args = append(cmd.Args, "--no-owner", "--no-privileges")
	}

	// Опции включения/исключения
	if !task.IncludeSchema && !task.IncludeData {
		// Если оба выключены, включаем оба (по умолчанию)
	} else if !task.IncludeSchema {
		cmd.Args = append(cmd.Args, "--data-only")
	} else if !task.IncludeData {
		cmd.Args = append(cmd.Args, "--schema-only")
	}

	// Примечание: pg_dump не поддерживает флаг --roles-only
	// Роли нужно дампить отдельно через pg_dumpall --roles-only
	// Если нужно включить роли в дамп, это делается автоматически при дампе схемы
	// Для отдельного дампа ролей нужно использовать pg_dumpall --roles-only
	// Здесь мы просто пропускаем этот флаг, так как он не поддерживается в pg_dump
	// if task.IncludeRoles {
	//     cmd.Args = append(cmd.Args, "--roles-only")  // Не поддерживается в pg_dump
	// }

	// Tablespaces включены по умолчанию в pg_dump
	// Если нужно исключить tablespaces, используем --no-tablespaces
	if !task.IncludeTablespaces {
		cmd.Args = append(cmd.Args, "--no-tablespaces")
	}

	// Устанавливаем переменную окружения с паролем
	cmd.Env = append(os.Environ(), fmt.Sprintf("PGPASSWORD=%s", password))

	log.Infof("Executing pg_dump for database %s on %s:%d", task.Database, conn.Host, conn.Port)

	// Выполняем pg_dump
	output, err := cmd.CombinedOutput()
	if err != nil {
		errorMsg := string(output)
		if errorMsg == "" {
			errorMsg = err.Error()
		}
		log.Errorf("pg_dump failed: %s", errorMsg)
		result.Error = errorMsg
		return result, fmt.Errorf("pg_dump failed: %s", errorMsg)
	}

	// Проверяем размер файла
	stat, err := os.Stat(dumpPath)
	if err != nil {
		result.Error = fmt.Sprintf("Dump file was not created: %v", err)
		return result, fmt.Errorf(result.Error)
	}

	result.DumpSizeMB = float64(stat.Size()) / (1024 * 1024)
	result.DumpFilename = dumpFilename
	result.DumpPath = dumpPath

	// Загружаем в хранилище
	storagePath, err := uploadPostgresBackupToStorage(task, dumpPath, dbNameSafe, log)
	if err != nil {
		result.Error = fmt.Sprintf("Failed to upload to storage: %v", err)
		// Удаляем локальный файл даже при ошибке загрузки
		os.Remove(dumpPath)
		return result, fmt.Errorf(result.Error)
	}

	result.StoragePath = storagePath
	result.Success = true

	// Удаляем локальный файл после успешной загрузки
	if err := os.Remove(dumpPath); err != nil {
		log.Warnf("Failed to remove local dump file: %v", err)
	} else {
		log.Infof("Local dump file removed: %s", dumpPath)
	}

	return result, nil
}

// RestorePostgresBackup восстанавливает базу данных из резервной копии
func RestorePostgresBackup(task config.PostgresTask, conn config.PostgresConnection, storagePath string, targetDatabase string, log *logger.Logger) (*PostgresBackupResult, error) {
	result := &PostgresBackupResult{}

	// Расшифровываем пароль
	password := conn.Password

	// Определяем целевую БД
	restoreDB := targetDatabase
	if restoreDB == "" {
		restoreDB = task.Database
	}

	// Создаем временную директорию
	tempDir := "/tmp/postgres_backups"
	if err := os.MkdirAll(tempDir, 0755); err != nil {
		result.Error = fmt.Sprintf("Failed to create temp directory: %v", err)
		return result, fmt.Errorf(result.Error)
	}

	// Скачиваем файл из хранилища
	// Извлекаем имя файла из пути хранилища
	filename := filepath.Base(storagePath)
	if filename == "" || filename == "." {
		// Если не удалось извлечь имя, генерируем временное
		filename = fmt.Sprintf("restore_%d.dump", time.Now().Unix())
	}

	localFile := filepath.Join(tempDir, filename)

	// TODO: Реализовать скачивание из хранилища
	// Пока предполагаем, что файл уже доступен локально или нужно реализовать StorageManager
	log.Warnf("Storage download not yet implemented, assuming file is at: %s", storagePath)

	// Определяем формат по расширению
	fileExt := strings.ToLower(filepath.Ext(localFile))
	var restoreCmd string
	if fileExt == ".dump" || fileExt == ".custom" || fileExt == ".tar" {
		restoreCmd = "pg_restore"
	} else {
		restoreCmd = "psql"
	}

	// Формируем команду восстановления
	var cmd *exec.Cmd
	if restoreCmd == "pg_restore" {
		cmd = exec.Command(
			"pg_restore",
			fmt.Sprintf("--host=%s", conn.Host),
			fmt.Sprintf("--port=%d", conn.Port),
			fmt.Sprintf("--username=%s", conn.Username),
			fmt.Sprintf("--dbname=%s", restoreDB),
			"--clean",
			"--if-exists",
			localFile,
		)
	} else {
		// Для plain SQL используем psql
		cmd = exec.Command(
			"psql",
			fmt.Sprintf("--host=%s", conn.Host),
			fmt.Sprintf("--port=%d", conn.Port),
			fmt.Sprintf("--username=%s", conn.Username),
			fmt.Sprintf("--dbname=%s", restoreDB),
			"--file", localFile,
		)
	}

	cmd.Env = append(os.Environ(), fmt.Sprintf("PGPASSWORD=%s", password))

	log.Infof("Restoring database %s from %s", restoreDB, localFile)

	// Выполняем восстановление
	output, err := cmd.CombinedOutput()
	if err != nil {
		errorMsg := string(output)
		if errorMsg == "" {
			errorMsg = err.Error()
		}
		log.Errorf("Restore failed: %s", errorMsg)
		result.Error = errorMsg
		return result, fmt.Errorf("restore failed: %s", errorMsg)
	}

	result.Success = true
	log.Infof("Database %s restored successfully", restoreDB)

	// Удаляем локальный файл
	if err := os.Remove(localFile); err != nil {
		log.Warnf("Failed to remove local restore file: %v", err)
	}

	return result, nil
}

// uploadPostgresBackupToStorage загружает дамп в хранилище
func uploadPostgresBackupToStorage(task config.PostgresTask, dumpPath string, dbNameSafe string, log *logger.Logger) (string, error) {
	// Парсим конфигурацию хранилища из JSON
	var storageConfig map[string]interface{}
	if err := json.Unmarshal([]byte(task.StorageConfig), &storageConfig); err != nil {
		return "", fmt.Errorf("failed to parse storage config: %v", err)
	}

	storageType := task.StorageType
	if storageType == "" {
		storageType = "s3" // По умолчанию S3
	}

	// Формируем путь в хранилище
	remotePath := fmt.Sprintf("postgres_backups/%s/%s", dbNameSafe, filepath.Base(dumpPath))

	switch storageType {
	case "s3":
		return uploadPostgresBackupToS3(storageConfig, dumpPath, remotePath, log)
	case "local":
		return uploadPostgresBackupToLocalAgent(storageConfig, dumpPath, remotePath, log)
	default:
		return "", fmt.Errorf("storage type %s not yet implemented in agent", storageType)
	}
}

// uploadPostgresBackupToS3 загружает дамп в S3 хранилище
func uploadPostgresBackupToS3(storageConfig map[string]interface{}, dumpPath string, remotePath string, log *logger.Logger) (string, error) {
	// Извлекаем параметры S3 из конфигурации
	endpoint, ok := storageConfig["endpoint"].(string)
	if !ok {
		return "", fmt.Errorf("endpoint not found in storage config")
	}

	accessKey, ok := storageConfig["access_key"].(string)
	if !ok {
		return "", fmt.Errorf("access_key not found in storage config")
	}

	secretKey, ok := storageConfig["secret_key"].(string)
	if !ok {
		return "", fmt.Errorf("secret_key not found in storage config")
	}

	bucketName, ok := storageConfig["bucket_name"].(string)
	if !ok {
		return "", fmt.Errorf("bucket_name not found in storage config")
	}

	region, _ := storageConfig["region"].(string)
	if region == "" {
		region = "us-east-1"
	}

	useSSL := false
	if ssl, ok := storageConfig["use_ssl"].(bool); ok {
		useSSL = ssl
	} else if strings.HasPrefix(endpoint, "https://") {
		useSSL = true
	}

	log.Infof("Uploading PostgreSQL backup to S3: %s/%s", endpoint, bucketName)

	// Очищаем endpoint от протокола
	endpoint = strings.TrimPrefix(strings.TrimPrefix(endpoint, "http://"), "https://")

	// Создаем клиент MinIO
	minioClient, err := minio.New(endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(accessKey, secretKey, ""),
		Secure: useSSL,
		Region: region,
	})
	if err != nil {
		return "", fmt.Errorf("failed to create S3 client: %v", err)
	}

	ctx := context.Background()

	// Проверяем существование bucket
	exists, err := minioClient.BucketExists(ctx, bucketName)
	if err != nil {
		return "", fmt.Errorf("failed to check bucket: %v", err)
	}

	if !exists {
		if err := minioClient.MakeBucket(ctx, bucketName, minio.MakeBucketOptions{Region: region}); err != nil {
			return "", fmt.Errorf("failed to create bucket: %v", err)
		}
	}

	// Загружаем файл
	_, err = minioClient.FPutObject(ctx, bucketName, remotePath, dumpPath, minio.PutObjectOptions{})
	if err != nil {
		return "", fmt.Errorf("failed to upload file: %v", err)
	}

	s3Path := fmt.Sprintf("s3://%s/%s", bucketName, remotePath)
	log.Infof("Successfully uploaded PostgreSQL backup to %s", s3Path)

	return s3Path, nil
}

// uploadPostgresBackupToLocalAgent загружает дамп на другой агент
func uploadPostgresBackupToLocalAgent(storageConfig map[string]interface{}, dumpPath string, remotePath string, log *logger.Logger) (string, error) {
	agentIP, ok := storageConfig["agent_ip"].(string)
	if !ok {
		return "", fmt.Errorf("agent_ip not found in storage config")
	}

	agentPort, ok := storageConfig["agent_port"].(float64)
	if !ok {
		return "", fmt.Errorf("agent_port not found in storage config")
	}

	basePath, ok := storageConfig["base_path"].(string)
	if !ok {
		return "", fmt.Errorf("base_path not found in storage config")
	}

	// Формируем полный путь на агенте
	fullPath := filepath.Join(basePath, remotePath)
	agentURL := fmt.Sprintf("http://%s:%d/api/storage/upload?base_path=%s&remote_path=%s",
		agentIP, int(agentPort), url.QueryEscape(basePath), url.QueryEscape(remotePath))

	log.Infof("Uploading PostgreSQL backup to local agent: %s", agentURL)

	// Открываем файл
	file, err := os.Open(dumpPath)
	if err != nil {
		return "", fmt.Errorf("failed to open dump file: %v", err)
	}
	defer file.Close()

	// Создаем multipart form
	var b bytes.Buffer
	writer := multipart.NewWriter(&b)
	part, err := writer.CreateFormFile("file", filepath.Base(dumpPath))
	if err != nil {
		return "", fmt.Errorf("failed to create form file: %v", err)
	}

	if _, err := io.Copy(part, file); err != nil {
		return "", fmt.Errorf("failed to copy file: %v", err)
	}
	writer.Close()

	// Отправляем запрос
	resp, err := http.Post(agentURL, writer.FormDataContentType(), &b)
	if err != nil {
		return "", fmt.Errorf("failed to upload file: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("upload failed with status %d: %s", resp.StatusCode, string(body))
	}

	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("failed to decode response: %v", err)
	}

	storagePath, ok := result["path"].(string)
	if !ok {
		return "", fmt.Errorf("path not found in response")
	}

	log.Infof("Successfully uploaded PostgreSQL backup to local agent: %s", storagePath)
	return storagePath, nil
}



