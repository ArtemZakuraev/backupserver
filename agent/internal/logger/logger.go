package logger

import (
	"log"
	"os"
)

type Logger struct {
	*log.Logger
}

func New() *Logger {
	return &Logger{
		Logger: log.New(os.Stdout, "[AGENT] ", log.LstdFlags|log.Lshortfile),
	}
}

func (l *Logger) Info(v ...interface{}) {
	l.Logger.Println(append([]interface{}{"INFO:"}, v...)...)
}

func (l *Logger) Infof(format string, v ...interface{}) {
	l.Logger.Printf("INFO: "+format, v...)
}

func (l *Logger) Warn(v ...interface{}) {
	l.Logger.Println(append([]interface{}{"WARN:"}, v...)...)
}

func (l *Logger) Warnf(format string, v ...interface{}) {
	l.Logger.Printf("WARN: "+format, v...)
}

func (l *Logger) Error(v ...interface{}) {
	l.Logger.Println(append([]interface{}{"ERROR:"}, v...)...)
}

func (l *Logger) Errorf(format string, v ...interface{}) {
	l.Logger.Printf("ERROR: "+format, v...)
}

func (l *Logger) Fatal(v ...interface{}) {
	l.Logger.Fatal(append([]interface{}{"FATAL:"}, v...)...)
}

func (l *Logger) Fatalf(format string, v ...interface{}) {
	l.Logger.Fatalf("FATAL: "+format, v...)
}







