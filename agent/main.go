package main

import (
	"backup-server-agent/internal/api"
	"backup-server-agent/internal/backup"
	"backup-server-agent/internal/config"
	"backup-server-agent/internal/cron"
	"backup-server-agent/internal/logger"
	"context"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	// Обработка флагов командной строки для выполнения задач из cron
	taskID := flag.Int("task-id", 0, "Task ID to execute")
	flag.Parse()

	// Инициализация логгера
	log := logger.New()

	// Загрузка конфигурации
	cfg, err := config.Load()
	if err != nil {
		log.Fatalf("Failed to load config: %v", err)
	}

	// Если указан task-id, выполняем задачу и выходим
	if *taskID > 0 {
		task := cfg.GetTask(*taskID)
		if task == nil {
			log.Fatalf("Task %d not found", *taskID)
		}
		log.Infof("Executing task %d from cron", *taskID)
		// Получаем IP сервера из конфига
		serverIP := cfg.ServerIP
		if serverIP == "" {
			serverIP = "unknown"
		}
		result, err := backup.ExecuteBackup(*task, serverIP, log)
		if err != nil {
			log.Fatalf("Task execution failed: %v", err)
		}
		if result.Success {
			log.Infof("Task completed successfully. Size: %d bytes, Files: %d", result.ArchiveSize, result.FilesCount)
		}
		os.Exit(0)
	}

	log.Infof("Backup Server Agent starting on port %d", cfg.Port)
	log.Infof("Server IP: %s, Server Hostname: %s", cfg.ServerIP, cfg.ServerHostname)

	// Инициализация планировщика cron
	cronManager := cron.New(cfg, log)
	if err := cronManager.LoadTasks(); err != nil {
		log.Warnf("Failed to load cron tasks: %v", err)
	}
	cronManager.Start()
	defer cronManager.Stop()

	// Создание HTTP сервера
	router := api.NewRouter(cfg, log, cronManager)
	srv := &http.Server{
		Addr:         fmt.Sprintf(":%d", cfg.Port),
		Handler:      router,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 15 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Запуск сервера в горутине
	go func() {
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Failed to start server: %v", err)
		}
	}()

	log.Info("Agent is running and ready to accept connections")

	// Ожидание сигнала для graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Info("Shutting down agent...")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Fatalf("Server forced to shutdown: %v", err)
	}

	log.Info("Agent stopped")
}

