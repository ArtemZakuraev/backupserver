package backup

import (
	"backup-server-agent/internal/logger"
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

type BackupRecord struct {
	SourcePath    string    `json:"source_path"`
	ArchiveName   string    `json:"archive_name"`
	BackupDate    time.Time `json:"backup_date"`
	S3UploadDate  *time.Time `json:"s3_upload_date,omitempty"`
	ArchiveSizeMB float64   `json:"archive_size_mb"`
	S3Path        string    `json:"s3_path,omitempty"`
	Status        string    `json:"status"`
}

const backupStorageFile = "/var/lib/backup-server-agent/backups.json"

func SaveBackupRecord(record BackupRecord, log *logger.Logger) error {
	// Создаем директорию если её нет
	dir := filepath.Dir(backupStorageFile)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}

	// Читаем существующие записи
	records := []BackupRecord{}
	if data, err := os.ReadFile(backupStorageFile); err == nil {
		json.Unmarshal(data, &records)
	}

	// Добавляем новую запись
	records = append(records, record)

	// Сохраняем
	data, err := json.MarshalIndent(records, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(backupStorageFile, data, 0644)
}

func GetBackupRecords() ([]BackupRecord, error) {
	records := []BackupRecord{}
	
	if data, err := os.ReadFile(backupStorageFile); err != nil {
		return records, nil // Файл не существует, возвращаем пустой список
	} else {
		if err := json.Unmarshal(data, &records); err != nil {
			return records, err
		}
	}

	return records, nil
}

func UpdateBackupRecord(archiveName string, s3Path string, uploadDate time.Time) error {
	records, err := GetBackupRecords()
	if err != nil {
		return err
	}

	// Обновляем запись
	for i := range records {
		if records[i].ArchiveName == archiveName {
			records[i].S3Path = s3Path
			records[i].S3UploadDate = &uploadDate
			records[i].Status = "success"
			break
		}
	}

	// Сохраняем
	data, err := json.MarshalIndent(records, "", "  ")
	if err != nil {
		return err
	}

	return os.WriteFile(backupStorageFile, data, 0644)
}




