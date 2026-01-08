package cron

import (
	"fmt"
	"os/exec"
	"strings"

	"backup-server-agent/internal/backup"
	"backup-server-agent/internal/config"
	"backup-server-agent/internal/logger"
	"github.com/robfig/cron/v3"
)

type CronManager struct {
	cron              *cron.Cron
	config            *config.Config
	logger            *logger.Logger
	entries           map[int]cron.EntryID
	postgresEntries   map[int]cron.EntryID
}

func New(cfg *config.Config, log *logger.Logger) *CronManager {
	return &CronManager{
		cron:            cron.New(cron.WithSeconds()),
		config:          cfg,
		logger:          log,
		entries:         make(map[int]cron.EntryID),
		postgresEntries: make(map[int]cron.EntryID),
	}
}

func (cm *CronManager) Start() {
	cm.cron.Start()
	cm.logger.Info("Cron manager started")
}

func (cm *CronManager) Stop() {
	cm.cron.Stop()
	cm.logger.Info("Cron manager stopped")
}

func (cm *CronManager) LoadTasks() error {
	// Загружаем обычные задачи
	for _, task := range cm.config.Tasks {
		if task.ScheduleCron != "" {
			cm.AddTask(task)
		}
	}
	
	// Загружаем PostgreSQL задачи
	for _, task := range cm.config.PostgresTasks {
		if task.ScheduleCron != "" {
			cm.AddPostgresTask(task)
		}
	}
	
	return nil
}

func (cm *CronManager) AddTask(task config.Task) {
	// Удаляем старую задачу, если есть
	if entryID, exists := cm.entries[task.TaskID]; exists {
		cm.cron.Remove(entryID)
	}

	// Добавляем новую задачу
	entryID, err := cm.cron.AddFunc(task.ScheduleCron, func() {
		cm.logger.Infof("Executing backup task %d: %s", task.TaskID, task.SourcePath)
		serverIP := cm.config.ServerIP
		if serverIP == "" {
			serverIP = "unknown"
		}
		result, err := backup.ExecuteBackup(task, serverIP, cm.logger)
		if err != nil {
			cm.logger.Errorf("Backup task %d failed: %v", task.TaskID, err)
		} else if result.Success {
			cm.logger.Infof("Backup task %d completed successfully. Size: %d bytes, Files: %d", 
				task.TaskID, result.ArchiveSize, result.FilesCount)
		}
	})

	if err != nil {
		cm.logger.Errorf("Failed to add cron task %d: %v", task.TaskID, err)
		return
	}

	cm.entries[task.TaskID] = entryID
	cm.logger.Infof("Added cron task %d with schedule: %s", task.TaskID, task.ScheduleCron)

	// Обновляем системный cron
	cm.updateSystemCron(task)
}

func (cm *CronManager) RemoveTask(taskID int) {
	if entryID, exists := cm.entries[taskID]; exists {
		cm.cron.Remove(entryID)
		delete(cm.entries, taskID)
		cm.logger.Infof("Removed cron task %d", taskID)
	}

	// Удаляем из системного cron
	cm.removeFromSystemCron(taskID)
}

func (cm *CronManager) updateSystemCron(task config.Task) {
	// Получаем путь к исполняемому файлу агента
	execPath := "/usr/bin/backup-server-agent"
	
	// Формируем команду для cron
	// Конвертируем cron с секундами в стандартный формат (без секунд)
	cronExpr := task.ScheduleCron
	parts := strings.Fields(cronExpr)
	if len(parts) == 6 {
		// Убираем секунды (первый элемент)
		cronExpr = strings.Join(parts[1:], " ")
	}

	cronLine := fmt.Sprintf("%s %s --task-id %d",
		cronExpr, execPath, task.TaskID)

	// Читаем текущий crontab
	cmd := exec.Command("crontab", "-l")
	currentCrontab, _ := cmd.Output()

	crontabContent := string(currentCrontab)

	// Удаляем старую запись для этой задачи, если есть
	lines := strings.Split(crontabContent, "\n")
	var newLines []string
	marker := fmt.Sprintf("--task-id %d", task.TaskID)
	for _, line := range lines {
		if !strings.Contains(line, marker) {
			newLines = append(newLines, line)
		}
	}

	// Добавляем новую запись
	newLines = append(newLines, cronLine)

	// Записываем обновленный crontab
	cmd = exec.Command("crontab", "-")
	cmd.Stdin = strings.NewReader(strings.Join(newLines, "\n") + "\n")
	if err := cmd.Run(); err != nil {
		cm.logger.Warnf("Failed to update system crontab: %v", err)
	} else {
		cm.logger.Infof("Updated system crontab for task %d", task.TaskID)
	}
}

func (cm *CronManager) removeFromSystemCron(taskID int) {
	cmd := exec.Command("crontab", "-l")
	currentCrontab, _ := cmd.Output()

	lines := strings.Split(string(currentCrontab), "\n")
	var newLines []string
	marker := fmt.Sprintf("--task-id %d", taskID)

	for _, line := range lines {
		if !strings.Contains(line, marker) {
			newLines = append(newLines, line)
		}
	}

	cmd = exec.Command("crontab", "-")
	cmd.Stdin = strings.NewReader(strings.Join(newLines, "\n") + "\n")
	if err := cmd.Run(); err != nil {
		cm.logger.Warnf("Failed to remove from system crontab: %v", err)
	} else {
		cm.logger.Infof("Removed task %d from system crontab", taskID)
	}
}

// PostgreSQL task management

func (cm *CronManager) AddPostgresTask(task config.PostgresTask) {
	// Удаляем старую задачу, если есть
	if entryID, exists := cm.postgresEntries[task.TaskID]; exists {
		cm.cron.Remove(entryID)
	}

	// Добавляем новую задачу
	entryID, err := cm.cron.AddFunc(task.ScheduleCron, func() {
		cm.logger.Infof("Executing PostgreSQL backup task %d: %s", task.TaskID, task.Database)
		
		// Получаем подключение к БД
		conn := cm.config.GetPostgresConnection(task.ConnectionID)
		if conn == nil {
			cm.logger.Errorf("PostgreSQL connection %d not found for task %d", task.ConnectionID, task.TaskID)
			return
		}

		serverIP := cm.config.ServerIP
		if serverIP == "" {
			serverIP = "unknown"
		}

		result, err := backup.ExecutePostgresBackup(task, *conn, serverIP, cm.logger)
		if err != nil {
			cm.logger.Errorf("PostgreSQL backup task %d failed: %v", task.TaskID, err)
		} else if result.Success {
			cm.logger.Infof("PostgreSQL backup task %d completed successfully. Size: %.2f MB", 
				task.TaskID, result.DumpSizeMB)
		}
	})

	if err != nil {
		cm.logger.Errorf("Failed to add PostgreSQL cron task %d: %v", task.TaskID, err)
		return
	}

	cm.postgresEntries[task.TaskID] = entryID
	cm.logger.Infof("Added PostgreSQL cron task %d with schedule: %s", task.TaskID, task.ScheduleCron)

	// Обновляем системный cron
	cm.updateSystemCronForPostgres(task)
}

func (cm *CronManager) RemovePostgresTask(taskID int) {
	if entryID, exists := cm.postgresEntries[taskID]; exists {
		cm.cron.Remove(entryID)
		delete(cm.postgresEntries, taskID)
		cm.logger.Infof("Removed PostgreSQL cron task %d", taskID)
	}

	// Удаляем из системного cron
	cm.removeFromSystemCronForPostgres(taskID)
}

func (cm *CronManager) updateSystemCronForPostgres(task config.PostgresTask) {
	execPath := "/usr/bin/backup-server-agent"
	
	cronExpr := task.ScheduleCron
	parts := strings.Fields(cronExpr)
	if len(parts) == 6 {
		cronExpr = strings.Join(parts[1:], " ")
	}

	// Для PostgreSQL задач используем специальный флаг
	cronLine := fmt.Sprintf("%s %s --postgres-task-id %d",
		cronExpr, execPath, task.TaskID)

	cmd := exec.Command("crontab", "-l")
	currentCrontab, _ := cmd.Output()

	crontabContent := string(currentCrontab)
	lines := strings.Split(crontabContent, "\n")
	var newLines []string
	marker := fmt.Sprintf("--postgres-task-id %d", task.TaskID)
	for _, line := range lines {
		if !strings.Contains(line, marker) {
			newLines = append(newLines, line)
		}
	}

	newLines = append(newLines, cronLine)

	cmd = exec.Command("crontab", "-")
	cmd.Stdin = strings.NewReader(strings.Join(newLines, "\n") + "\n")
	if err := cmd.Run(); err != nil {
		cm.logger.Warnf("Failed to update system crontab for PostgreSQL task: %v", err)
	} else {
		cm.logger.Infof("Updated system crontab for PostgreSQL task %d", task.TaskID)
	}
}

func (cm *CronManager) removeFromSystemCronForPostgres(taskID int) {
	cmd := exec.Command("crontab", "-l")
	currentCrontab, _ := cmd.Output()

	lines := strings.Split(string(currentCrontab), "\n")
	var newLines []string
	marker := fmt.Sprintf("--postgres-task-id %d", taskID)

	for _, line := range lines {
		if !strings.Contains(line, marker) {
			newLines = append(newLines, line)
		}
	}

	cmd = exec.Command("crontab", "-")
	cmd.Stdin = strings.NewReader(strings.Join(newLines, "\n") + "\n")
	if err := cmd.Run(); err != nil {
		cm.logger.Warnf("Failed to remove PostgreSQL task from system crontab: %v", err)
	} else {
		cm.logger.Infof("Removed PostgreSQL task %d from system crontab", taskID)
	}
}

