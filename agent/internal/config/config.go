package config

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

const (
	ConfigDir  = "/etc/backupserveragent"
	ConfigFile = "config.json"
)

type Config struct {
	Port           int    `json:"port"`
	ServerIP       string `json:"server_ip"`
	ServerHostname string `json:"server_hostname"`
	Tasks          []Task `json:"tasks"`
	// Настройки хранилища по умолчанию для агента
	DefaultStorageType   string `json:"default_storage_type,omitempty"`   // s3, sftp, nfs, local
	DefaultStorageConfig string `json:"default_storage_config,omitempty"` // JSON строка с настройками хранилища
	// Настройки подключения к PostgreSQL
	PostgresConnections []PostgresConnection `json:"postgres_connections,omitempty"`
	PostgresTasks       []PostgresTask       `json:"postgres_tasks,omitempty"`
}

type Task struct {
	TaskID           int    `json:"task_id"`
	SourcePath       string `json:"source_path"`
	CreateArchive    bool   `json:"create_archive"`
	ArchiveFormat    string `json:"archive_format"`
	// S3 настройки (для обратной совместимости)
	S3Endpoint       string `json:"s3_endpoint"`
	S3AccessKey      string `json:"s3_access_key"`
	S3SecretKey      string `json:"s3_secret_key"`
	S3Bucket         string `json:"s3_bucket"`
	S3Region         string `json:"s3_region"`
	// Универсальные настройки хранилища
	StorageType      string `json:"storage_type"` // s3, sftp, nfs, local
	StorageConfig    string `json:"storage_config"` // JSON строка с настройками хранилища
	CleanupEnabled   bool   `json:"cleanup_enabled"`
	CleanupDays      int    `json:"cleanup_days"`
	IsDockerCompose  bool   `json:"is_docker_compose"`
	DockerComposePath string `json:"docker_compose_path"`
	ScheduleCron     string `json:"schedule_cron"`
}

// PostgresConnection хранит настройки подключения к PostgreSQL
type PostgresConnection struct {
	ID       int    `json:"id"`       // ID задачи на сервере
	Host     string `json:"host"`
	Port     int    `json:"port"`
	Username string `json:"username"`
	Password string `json:"password"` // Зашифрованный пароль
	Database string `json:"database"`
}

// PostgresTask хранит задачу резервного копирования PostgreSQL
type PostgresTask struct {
	TaskID           int    `json:"task_id"`           // ID задачи на сервере
	ConnectionID     int    `json:"connection_id"`     // ID подключения к БД
	Name             string `json:"name"`
	Database         string `json:"database"`
	BackupFormat     string `json:"backup_format"`     // custom, plain, tar, directory
	CompressionLevel int    `json:"compression_level"` // 0-9
	IncludeSchema    bool   `json:"include_schema"`
	IncludeData      bool   `json:"include_data"`
	IncludeRoles     bool   `json:"include_roles"`
	IncludeTablespaces bool `json:"include_tablespaces"`
	StorageType      string `json:"storage_type"`     // s3, sftp, nfs, local
	StorageConfig    string `json:"storage_config"`   // JSON строка с настройками хранилища
	CleanupEnabled   bool   `json:"cleanup_enabled"`
	CleanupDays      int    `json:"cleanup_days"`
	ScheduleCron     string `json:"schedule_cron"`
}

func Load() (*Config, error) {
	configPath := filepath.Join(ConfigDir, ConfigFile)

	// Если файл не существует, создаем конфигурацию по умолчанию
	if _, err := os.Stat(configPath); os.IsNotExist(err) {
		cfg := &Config{
			Port:           11540,
			ServerIP:       "",
			ServerHostname: "",
			Tasks:          []Task{},
		}
		if err := Save(cfg); err != nil {
			return nil, fmt.Errorf("failed to create default config: %v", err)
		}
		return cfg, nil
	}

	data, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("failed to read config: %v", err)
	}

	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("failed to parse config: %v", err)
	}

	return &cfg, nil
}

func Save(cfg *Config) error {
	// Создаем директорию, если её нет
	if err := os.MkdirAll(ConfigDir, 0755); err != nil {
		return fmt.Errorf("failed to create config directory: %v", err)
	}

	configPath := filepath.Join(ConfigDir, ConfigFile)
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return fmt.Errorf("failed to marshal config: %v", err)
	}

	if err := os.WriteFile(configPath, data, 0644); err != nil {
		return fmt.Errorf("failed to write config: %v", err)
	}

	return nil
}

func (c *Config) AddOrUpdateTask(task Task) {
	for i, t := range c.Tasks {
		if t.TaskID == task.TaskID {
			c.Tasks[i] = task
			return
		}
	}
	c.Tasks = append(c.Tasks, task)
}

func (c *Config) RemoveTask(taskID int) {
	var newTasks []Task
	for _, t := range c.Tasks {
		if t.TaskID != taskID {
			newTasks = append(newTasks, t)
		}
	}
	c.Tasks = newTasks
}

func (c *Config) GetTask(taskID int) *Task {
	for _, t := range c.Tasks {
		if t.TaskID == taskID {
			return &t
		}
	}
	return nil
}

func (c *Config) AddOrUpdatePostgresConnection(conn PostgresConnection) {
	for i, pc := range c.PostgresConnections {
		if pc.ID == conn.ID {
			c.PostgresConnections[i] = conn
			return
		}
	}
	c.PostgresConnections = append(c.PostgresConnections, conn)
}

func (c *Config) RemovePostgresConnection(connID int) {
	var newConns []PostgresConnection
	for _, pc := range c.PostgresConnections {
		if pc.ID != connID {
			newConns = append(newConns, pc)
		}
	}
	c.PostgresConnections = newConns
}

func (c *Config) GetPostgresConnection(connID int) *PostgresConnection {
	for i := range c.PostgresConnections {
		if c.PostgresConnections[i].ID == connID {
			return &c.PostgresConnections[i]
		}
	}
	return nil
}

// Методы для работы с PostgreSQL задачами в структуре Config
func (c *Config) AddOrUpdatePostgresTask(task PostgresTask) {
	for i, t := range c.PostgresTasks {
		if t.TaskID == task.TaskID {
			c.PostgresTasks[i] = task
			return
		}
	}
	c.PostgresTasks = append(c.PostgresTasks, task)
}

func (c *Config) RemovePostgresTask(taskID int) {
	var newTasks []PostgresTask
	for _, t := range c.PostgresTasks {
		if t.TaskID != taskID {
			newTasks = append(newTasks, t)
		}
	}
	c.PostgresTasks = newTasks
}

func (c *Config) GetPostgresTask(taskID int) *PostgresTask {
	for i := range c.PostgresTasks {
		if c.PostgresTasks[i].TaskID == taskID {
			return &c.PostgresTasks[i]
		}
	}
	return nil
}

// Глобальные функции для обратной совместимости (используют глобальную переменную)
// ВАЖНО: Эти функции устарели, используйте методы Config
var globalPostgresTasks []PostgresTask

func AddOrUpdatePostgresTask(task PostgresTask) {
	globalPostgresTasks = append(globalPostgresTasks, task)
}

func GetPostgresTask(taskID int) *PostgresTask {
	for i := range globalPostgresTasks {
		if globalPostgresTasks[i].TaskID == taskID {
			return &globalPostgresTasks[i]
		}
	}
	return nil
}







