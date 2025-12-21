package backup

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"backup-server-agent/internal/config"
	"backup-server-agent/internal/logger"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

type BackupResult struct {
	Success      bool
	ArchivePath  string
	ArchiveSize  int64
	FilesCount   int
	Error        string
	S3Path       string
}

func ExecuteBackup(task config.Task, serverIP string, log *logger.Logger) (*BackupResult, error) {
	result := &BackupResult{}

	// Если это Docker Compose проект, останавливаем его
	var dockerComposeDir string
	if task.IsDockerCompose && task.DockerComposePath != "" {
		log.Infof("Stopping Docker Compose project: %s", task.DockerComposePath)
		dockerComposeDir = filepath.Dir(task.DockerComposePath)
		
		// Пробуем docker compose (новая версия)
		cmd := exec.Command("docker", "compose", "-f", task.DockerComposePath, "down")
		cmd.Dir = dockerComposeDir
		if err := cmd.Run(); err != nil {
			// Пробуем docker-compose (старая версия)
			cmd := exec.Command("docker-compose", "-f", task.DockerComposePath, "down")
			cmd.Dir = dockerComposeDir
			if err := cmd.Run(); err != nil {
				log.Warnf("Failed to stop docker-compose: %v", err)
			}
		}
		
		defer func() {
			// Запускаем обратно после завершения
			log.Infof("Starting Docker Compose project: %s", task.DockerComposePath)
			cmd := exec.Command("docker", "compose", "-f", task.DockerComposePath, "up", "-d")
			cmd.Dir = dockerComposeDir
			if err := cmd.Run(); err != nil {
				cmd := exec.Command("docker-compose", "-f", task.DockerComposePath, "up", "-d")
				cmd.Dir = dockerComposeDir
				if err := cmd.Run(); err != nil {
					log.Errorf("Failed to start docker-compose: %v", err)
				}
			}
		}()
	}

	// Создаем архив, если нужно
	var archiveName string
	if task.CreateArchive {
		archivePath, name, err := createArchive(task.SourcePath, task.ArchiveFormat, serverIP, log)
		if err != nil {
			result.Error = fmt.Sprintf("Failed to create archive: %v", err)
			return result, err
		}
		result.ArchivePath = archivePath
		archiveName = name
		
		// Сохраняем запись о бэкапе
		backupRecord := BackupRecord{
			SourcePath:    task.SourcePath,
			ArchiveName:   archiveName,
			BackupDate:    time.Now(),
			ArchiveSizeMB: 0, // Будет обновлено после создания
			Status:        "creating",
		}
		if err := SaveBackupRecord(backupRecord, log); err != nil {
			log.Warnf("Failed to save backup record: %v", err)
		}

		// Получаем размер архива
		stat, err := os.Stat(result.ArchivePath)
		if err == nil {
			result.ArchiveSize = stat.Size()
			// Обновляем размер в записи
			records, _ := GetBackupRecords()
			for i := range records {
				if records[i].ArchiveName == archiveName {
					records[i].ArchiveSizeMB = float64(result.ArchiveSize) / (1024 * 1024)
					break
				}
			}
		}

		// Подсчитываем количество файлов
		result.FilesCount = countFiles(task.SourcePath)
	} else {
		// Если не создаем архив, просто считаем файлы
		result.FilesCount = countFiles(task.SourcePath)
	}

	// Загружаем в S3
	if task.S3Endpoint != "" && task.S3Bucket != "" {
		if result.ArchivePath != "" {
			// Загружаем архив
			s3Path, err := uploadToS3(task, result.ArchivePath, log)
			if err != nil {
				result.Error = fmt.Sprintf("Failed to upload to S3: %v", err)
				return result, err
			}
			result.S3Path = s3Path
			
			// Обновляем запись о бэкапе
			if err := UpdateBackupRecord(archiveName, s3Path, time.Now()); err != nil {
				log.Warnf("Failed to update backup record: %v", err)
			}
			
			// Удаляем локальный архив после успешной загрузки
			if err := os.Remove(result.ArchivePath); err != nil {
				log.Warnf("Failed to remove archive after upload: %v", err)
			} else {
				log.Infof("Archive removed after successful upload: %s", result.ArchivePath)
			}
		} else {
			// Загружаем файлы напрямую без архива
			s3Path, err := uploadDirectoryToS3(task, task.SourcePath, log)
			if err != nil {
				result.Error = fmt.Sprintf("Failed to upload directory to S3: %v", err)
				return result, err
			}
			result.S3Path = s3Path
		}
	}

	// Очистка старых бэкапов
	if task.CleanupEnabled {
		if err := cleanupOldBackups(task, log); err != nil {
			log.Warnf("Failed to cleanup old backups: %v", err)
		}
	}

	result.Success = true
	return result, nil
}

func createArchive(sourcePath, format, serverIP string, log *logger.Logger) (string, string, error) {
	timestamp := time.Now().Format("20060102_150405")
	// Имя архива: IP_сервера_путь_дата
	safePath := strings.ReplaceAll(strings.TrimPrefix(sourcePath, "/"), "/", "_")
	archiveName := fmt.Sprintf("%s_%s_%s.%s", serverIP, safePath, timestamp, format)
	archivePath := filepath.Join("/tmp", archiveName)

	log.Infof("Creating archive: %s from %s", archivePath, sourcePath)

	file, err := os.Create(archivePath)
	if err != nil {
		return "", "", err
	}
	defer file.Close()

	var writer io.Writer = file

	if format == "tar.gz" {
		gzipWriter := gzip.NewWriter(file)
		defer gzipWriter.Close()
		writer = gzipWriter
	}

	tarWriter := tar.NewWriter(writer)
	defer tarWriter.Close()

	err = filepath.Walk(sourcePath, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		header, err := tar.FileInfoHeader(info, "")
		if err != nil {
			return err
		}

		relPath, err := filepath.Rel(sourcePath, path)
		if err != nil {
			return err
		}
		header.Name = relPath

		if err := tarWriter.WriteHeader(header); err != nil {
			return err
		}

		if !info.Mode().IsRegular() {
			return nil
		}

		file, err := os.Open(path)
		if err != nil {
			return err
		}
		defer file.Close()

		_, err = io.Copy(tarWriter, file)
		return err
	})
	
	if err != nil {
		return "", "", err
	}
	
	return archivePath, archiveName, nil
}

func countFiles(path string) int {
	count := 0
	filepath.Walk(path, func(p string, info os.FileInfo, err error) error {
		if err == nil && info.Mode().IsRegular() {
			count++
		}
		return nil
	})
	return count
}

func uploadToS3(task config.Task, archivePath string, log *logger.Logger) (string, error) {
	log.Infof("Uploading to S3: %s/%s", task.S3Endpoint, task.S3Bucket)

	// Очищаем endpoint от протокола
	endpoint := strings.TrimPrefix(strings.TrimPrefix(task.S3Endpoint, "http://"), "https://")
	useSSL := strings.HasPrefix(task.S3Endpoint, "https://")

	// Создаем клиент MinIO
	minioClient, err := minio.New(endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(task.S3AccessKey, task.S3SecretKey, ""),
		Secure: useSSL,
		Region: task.S3Region,
	})
	if err != nil {
		return "", fmt.Errorf("failed to create S3 client: %v", err)
	}

	ctx := context.Background()

	// Проверяем существование bucket
	exists, err := minioClient.BucketExists(ctx, task.S3Bucket)
	if err != nil {
		return "", fmt.Errorf("failed to check bucket: %v", err)
	}

	if !exists {
		if err := minioClient.MakeBucket(ctx, task.S3Bucket, minio.MakeBucketOptions{Region: task.S3Region}); err != nil {
			return "", fmt.Errorf("failed to create bucket: %v", err)
		}
	}

	// Загружаем файл
	objectName := filepath.Base(archivePath)
	_, err = minioClient.FPutObject(ctx, task.S3Bucket, objectName, archivePath, minio.PutObjectOptions{})
	if err != nil {
		return "", fmt.Errorf("failed to upload file: %v", err)
	}

	s3Path := fmt.Sprintf("s3://%s/%s", task.S3Bucket, objectName)
	log.Infof("Successfully uploaded to %s", s3Path)

	return s3Path, nil
}

func uploadDirectoryToS3(task config.Task, sourcePath string, log *logger.Logger) (string, error) {
	log.Infof("Uploading directory to S3: %s/%s", task.S3Endpoint, task.S3Bucket)

	// Очищаем endpoint от протокола
	endpoint := strings.TrimPrefix(strings.TrimPrefix(task.S3Endpoint, "http://"), "https://")
	useSSL := strings.HasPrefix(task.S3Endpoint, "https://")

	minioClient, err := minio.New(endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(task.S3AccessKey, task.S3SecretKey, ""),
		Secure: useSSL,
		Region: task.S3Region,
	})
	if err != nil {
		return "", fmt.Errorf("failed to create S3 client: %v", err)
	}

	ctx := context.Background()

	exists, err := minioClient.BucketExists(ctx, task.S3Bucket)
	if err != nil {
		return "", fmt.Errorf("failed to check bucket: %v", err)
	}

	if !exists {
		if err := minioClient.MakeBucket(ctx, task.S3Bucket, minio.MakeBucketOptions{Region: task.S3Region}); err != nil {
			return "", fmt.Errorf("failed to create bucket: %v", err)
		}
	}

	// Загружаем все файлы из директории
	timestamp := time.Now().Format("20060102_150405")
	baseName := filepath.Base(sourcePath)

	err = filepath.Walk(sourcePath, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		if !info.Mode().IsRegular() {
			return nil
		}

		relPath, err := filepath.Rel(sourcePath, path)
		if err != nil {
			return err
		}

		objectName := fmt.Sprintf("%s_%s/%s", baseName, timestamp, relPath)
		_, err = minioClient.FPutObject(ctx, task.S3Bucket, objectName, path, minio.PutObjectOptions{})
		if err != nil {
			return fmt.Errorf("failed to upload %s: %v", path, err)
		}

		return nil
	})

	if err != nil {
		return "", err
	}

	s3Path := fmt.Sprintf("s3://%s/%s_%s/", task.S3Bucket, baseName, timestamp)
	log.Infof("Successfully uploaded directory to %s", s3Path)

	return s3Path, nil
}

func cleanupOldBackups(task config.Task, log *logger.Logger) error {
	log.Infof("Cleaning up backups older than %d days", task.CleanupDays)

	// Очищаем endpoint от протокола
	endpoint := strings.TrimPrefix(strings.TrimPrefix(task.S3Endpoint, "http://"), "https://")
	useSSL := strings.HasPrefix(task.S3Endpoint, "https://")

	minioClient, err := minio.New(endpoint, &minio.Options{
		Creds:  credentials.NewStaticV4(task.S3AccessKey, task.S3SecretKey, ""),
		Secure: useSSL,
		Region: task.S3Region,
	})
	if err != nil {
		return err
	}

	ctx := context.Background()
	cutoffTime := time.Now().AddDate(0, 0, -task.CleanupDays)

	objectsCh := minioClient.ListObjects(ctx, task.S3Bucket, minio.ListObjectsOptions{
		Recursive: true,
	})

	for object := range objectsCh {
		if object.Err != nil {
			continue
		}

		if object.LastModified.Before(cutoffTime) {
			log.Infof("Deleting old backup: %s", object.Key)
			if err := minioClient.RemoveObject(ctx, task.S3Bucket, object.Key, minio.RemoveObjectOptions{}); err != nil {
				log.Warnf("Failed to delete %s: %v", object.Key, err)
			}
		}
	}

	return nil
}

