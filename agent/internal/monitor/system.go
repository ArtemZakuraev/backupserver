package monitor

import (
	"bufio"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

type SystemInfo struct {
	DiskFreeGB      float64 `json:"disk_free_gb"`
	DiskTotalGB     float64 `json:"disk_total_gb"`
	MemoryFreeMB    float64 `json:"memory_free_mb"`
	MemoryTotalMB   float64 `json:"memory_total_mb"`
	CPULoadPercent  float64 `json:"cpu_load_percent"`
	NetworkRXMB     float64 `json:"network_rx_mb"`
	NetworkTXMB     float64 `json:"network_tx_mb"`
}

var (
	lastNetworkRX uint64
	lastNetworkTX uint64
	lastNetworkTime time.Time
)

func GetSystemInfo() (*SystemInfo, error) {
	info := &SystemInfo{}

	// Получение информации о диске
	if err := getDiskInfo(info); err != nil {
		return nil, err
	}

	// Получение информации о памяти
	if err := getMemoryInfo(info); err != nil {
		return nil, err
	}

	// Получение информации о CPU
	if err := getCPUInfo(info); err != nil {
		return nil, err
	}

	// Получение информации о сети
	if err := getNetworkInfo(info); err != nil {
		return nil, err
	}

	return info, nil
}

func getDiskInfo(info *SystemInfo) error {
	cmd := exec.Command("df", "-BG", "/")
	output, err := cmd.Output()
	if err != nil {
		return err
	}

	lines := strings.Split(string(output), "\n")
	if len(lines) < 2 {
		return nil
	}

	fields := strings.Fields(lines[1])
	if len(fields) < 4 {
		return nil
	}

	total, _ := strconv.ParseFloat(strings.TrimSuffix(fields[1], "G"), 64)
	available, _ := strconv.ParseFloat(strings.TrimSuffix(fields[3], "G"), 64)

	info.DiskTotalGB = total
	info.DiskFreeGB = available

	return nil
}

func getMemoryInfo(info *SystemInfo) error {
	file, err := os.Open("/proc/meminfo")
	if err != nil {
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	var memTotal, memAvailable uint64

	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "MemTotal:") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				memTotal, _ = strconv.ParseUint(fields[1], 10, 64)
			}
		} else if strings.HasPrefix(line, "MemAvailable:") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				memAvailable, _ = strconv.ParseUint(fields[1], 10, 64)
			}
		}
	}

	info.MemoryTotalMB = float64(memTotal) / 1024.0
	info.MemoryFreeMB = float64(memAvailable) / 1024.0

	return nil
}

func getCPUInfo(info *SystemInfo) error {
	file, err := os.Open("/proc/loadavg")
	if err != nil {
		return err
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	if scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) > 0 {
			load, _ := strconv.ParseFloat(fields[0], 64)
			// Получаем количество ядер CPU
			cmd := exec.Command("nproc")
			output, err := cmd.Output()
			if err == nil {
				cores, _ := strconv.Atoi(strings.TrimSpace(string(output)))
				if cores > 0 {
					info.CPULoadPercent = (load / float64(cores)) * 100.0
				}
			}
		}
	}

	return nil
}

func getNetworkInfo(info *SystemInfo) error {
	file, err := os.Open("/proc/net/dev")
	if err != nil {
		return err
	}
	defer file.Close()

	var rx, tx uint64
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Text()
		if strings.Contains(line, ":") {
			fields := strings.Fields(line)
			if len(fields) >= 10 {
				rxBytes, _ := strconv.ParseUint(fields[1], 10, 64)
				txBytes, _ := strconv.ParseUint(fields[9], 10, 64)
				rx += rxBytes
				tx += txBytes
			}
		}
	}

	now := time.Now()
	if !lastNetworkTime.IsZero() {
		elapsed := now.Sub(lastNetworkTime).Seconds()
		if elapsed > 0 {
			info.NetworkRXMB = float64(rx-lastNetworkRX) / 1024.0 / 1024.0 / elapsed
			info.NetworkTXMB = float64(tx-lastNetworkTX) / 1024.0 / 1024.0 / elapsed
		}
	}

	lastNetworkRX = rx
	lastNetworkTX = tx
	lastNetworkTime = now

	return nil
}

func GetFilesystemInfo(path string) (string, string, float64, float64, error) {
	// Получаем информацию о файловой системе для указанного пути
	cmd := exec.Command("df", "-BG", path)
	output, err := cmd.Output()
	if err != nil {
		return "", "", 0, 0, err
	}

	lines := strings.Split(string(output), "\n")
	if len(lines) < 2 {
		return "", "", 0, 0, nil
	}

	fields := strings.Fields(lines[1])
	if len(fields) < 6 {
		return "", "", 0, 0, nil
	}

	filesystem := fields[0]
	mountPoint := fields[5]
	total, _ := strconv.ParseFloat(strings.TrimSuffix(fields[1], "G"), 64)
	available, _ := strconv.ParseFloat(strings.TrimSuffix(fields[3], "G"), 64)

	return filesystem, mountPoint, total, available, nil
}

type DiskInfo struct {
	Device      string  `json:"device"`
	MountPoint  string  `json:"mount_point"`
	Filesystem  string  `json:"filesystem"`
	TotalGB     float64 `json:"total_gb"`
	UsedGB      float64 `json:"used_gb"`
	AvailableGB float64 `json:"available_gb"`
	UsedPercent float64 `json:"used_percent"`
}

func GetAllDisks() ([]DiskInfo, error) {
	// Получаем информацию о всех смонтированных дисках
	cmd := exec.Command("df", "-BG", "-T")
	output, err := cmd.Output()
	if err != nil {
		return nil, err
	}

	lines := strings.Split(string(output), "\n")
	if len(lines) < 2 {
		return []DiskInfo{}, nil
	}

	var disks []DiskInfo
	for i := 1; i < len(lines); i++ {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}

		fields := strings.Fields(line)
		if len(fields) < 7 {
			continue
		}

		// Пропускаем tmpfs, devtmpfs и другие виртуальные файловые системы
		filesystem := fields[1]
		if strings.HasPrefix(filesystem, "tmpfs") || 
		   strings.HasPrefix(filesystem, "devtmpfs") ||
		   strings.HasPrefix(filesystem, "sysfs") ||
		   strings.HasPrefix(filesystem, "proc") ||
		   strings.HasPrefix(filesystem, "devpts") ||
		   strings.HasPrefix(filesystem, "cgroup") ||
		   strings.HasPrefix(filesystem, "pstore") ||
		   strings.HasPrefix(filesystem, "bpf") ||
		   strings.HasPrefix(filesystem, "tracefs") ||
		   strings.HasPrefix(filesystem, "hugetlbfs") ||
		   strings.HasPrefix(filesystem, "mqueue") ||
		   strings.HasPrefix(filesystem, "debugfs") ||
		   strings.HasPrefix(filesystem, "securityfs") ||
		   strings.HasPrefix(filesystem, "configfs") ||
		   strings.HasPrefix(filesystem, "fusectl") ||
		   strings.HasPrefix(filesystem, "overlay") {
			continue
		}

		device := fields[0]
		mountPoint := fields[6]
		total, _ := strconv.ParseFloat(strings.TrimSuffix(fields[2], "G"), 64)
		used, _ := strconv.ParseFloat(strings.TrimSuffix(fields[3], "G"), 64)
		available, _ := strconv.ParseFloat(strings.TrimSuffix(fields[4], "G"), 64)

		var usedPercent float64
		if total > 0 {
			usedPercent = (used / total) * 100.0
		}

		disks = append(disks, DiskInfo{
			Device:      device,
			MountPoint:  mountPoint,
			Filesystem:  filesystem,
			TotalGB:     total,
			UsedGB:      used,
			AvailableGB: available,
			UsedPercent: usedPercent,
		})
	}

	return disks, nil
}







