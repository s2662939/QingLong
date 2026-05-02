# -*- coding: utf-8 -*-
"""
青龙面板文件管理系统 - 资源监控模块
基于群晖 DS918+ (DSM 7.3) 优化的文件管理自动化系统

=== A06_Resource.py 模块功能和中文注释 开始 ===

功能说明：
- CPU 使用率：实时监控、采样统计
- 内存监控：已用/可用内存、交换区
- 磁盘监控：空间、IO 读写速度
- 网络带宽：上传下载速度（可选）
- 硬件配置：DS918+ 硬件参数和阈值
- 动态调度：根据资源负载调整任务

【核心职责】
本模块是系统中唯一的资源监控模块，所有其他模块（如扫描器、哈希计算、
压缩模块等）需要获取系统资源状态时，必须调用此模块的方法。

【与 A01_Config.py 的关系】
- A01_Config.py：提供路径映射、文件夹定义、文件类型等基础配置
- A06_Resource.py：提供硬件配置、运行时资源监控和动态调度决策

=== A06_Resource.py 模块功能和中文注释 结束 ===
"""

import os
import time
import threading
from typing import Dict, List, Optional, Any, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque
from enum import Enum

try:
    from .A01_Config import Config
except ImportError:
    from A01_Config import Config


# ============================================================================
# DS918+ 硬件配置（从 A01_Config.py 重构至此）
# ============================================================================

class HardwareConfig:
    """DS918+ 硬件配置类"""
    
    # CPU 配置
    CPU_CORES: int = 4           # DS918+ 为 4 核 CPU
    MAX_WORKERS: int = 3         # 推荐最大工作线程数（根据资源使用情况放宽）
    MAX_WORKERS_CPU_INTENSIVE: int = 2  # CPU 密集型任务最大线程数
    
    # 内存配置
    TOTAL_MEMORY_MB: int = 4096   # DS918+ 总内存 4GB
    RESERVED_MEMORY_MB: int = 512  # 预留系统内存
    MAX_MEMORY_MB: int = TOTAL_MEMORY_MB - RESERVED_MEMORY_MB  # 可用内存 3.5GB
    MEMORY_THRESHOLD: float = 0.90  # 内存使用率阈值（90%，放宽以提升性能）
    
    # CPU 使用率阈值
    CPU_THRESHOLD: float = 0.85   # CPU 使用率阈值（85%，放宽以提升性能）
    
    # 磁盘 IO 配置
    BATCH_SIZE: int = 200         # 批量操作大小（放宽以提升性能）
    IO_CHUNK_SIZE: int = 8192     # IO 分块大小（8KB）
    HASH_CHUNK_SIZE: int = 65536  # 哈希计算分块大小（64KB）
    
    # 缓存配置
    HASH_CACHE_SIZE: int = 10000   # 哈希缓存大小
    METADATA_CACHE_SIZE: int = 5000  # 元数据缓存大小
    LRU_CACHE_SIZE: int = 1000     # LRU 缓存大小
    
    # 性能优化
    USE_QUICK_HASH: bool = True   # 是否使用快速哈希
    QUICK_HASH_SAMPLE_SIZE: int = 65536  # 快速哈希采样大小（64KB）
    
    # 监控配置
    MONITOR_INTERVAL: int = 60    # 监控间隔（秒）
    REPORT_INTERVAL: int = 300    # 报告生成间隔（秒）


def get_logger(name: str):
    """获取日志记录器"""
    try:
        return Config.get_instance().get_logger(name)
    except Exception:
        import logging
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger(name)


class ResourceType(Enum):
    """资源类型枚举"""
    CPU = "cpu"
    MEMORY = "memory"
    DISK = "disk"
    NETWORK = "network"


class TaskPriority(Enum):
    """任务优先级"""
    HIGH = 1      # 高优先级，可以抢占资源
    NORMAL = 2    # 普通优先级
    LOW = 3       # 低优先级，资源不足时暂停


@dataclass
class ResourceSnapshot:
    """资源快照数据类"""
    timestamp: datetime
    cpu_percent: float = 0.0
    memory_total: int = 0
    memory_available: int = 0
    memory_percent: float = 0.0
    disk_usage: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    def get_available_memory_mb(self) -> float:
        """获取可用内存（MB）"""
        return self.memory_available / (1024 * 1024)
    
    def get_cpu_cores_available(self) -> int:
        """获取可用 CPU 核心数"""
        try:
            cpu_count = psutil.cpu_count() if 'psutil' in dir() else 1
            return max(1, int(cpu_count * (1 - self.cpu_percent / 100)))
        except Exception:
            return 1


class ResourceLevel(Enum):
    """资源充足度等级"""
    NORMAL = "normal"       # 资源充足，正常运行
    LOW = "low"            # 资源偏低，需要降级
    CRITICAL = "critical"  # 资源紧张，极限降级


@dataclass
class ExecutionStrategy:
    """
    执行策略 - 资源不足时的降级策略
    
    【核心设计理念】
    资源不足时不是跳过任务，而是降低资源消耗确保任务完成：
    - 以前可以并行的 → 改为串行
    - 以前快速处理的 → 降低速度
    """
    level: ResourceLevel = ResourceLevel.NORMAL
    
    # 并发控制
    worker_count: int = 0           # 0=跟随系统自动调整
    max_workers: int = 4            # 最大工作线程数
    
    # 批次控制
    batch_size: int = 200           # 批量操作大小
    delay_between_batches: float = 0.0  # 批次间延迟(秒)
    
    # 内存控制
    max_memory_mb: int = 0          # 0=不限制
    
    # CPU 控制
    cpu_limit_percent: float = 0.0  # 0=不限制
    
    # 是否启用特定模式
    enable_throttle: bool = False    # 是否启用节流
    enable_serial_only: bool = False # 是否强制串行
    
    def is_serial_only(self) -> bool:
        """是否应该串行处理"""
        return self.enable_serial_only or self.worker_count == 1
    
    def get_adjusted_batch_size(self, original_size: int) -> int:
        """获取调整后的批次大小"""
        return min(original_size, self.batch_size)
    
    def should_add_delay(self) -> bool:
        """是否应该添加延迟"""
        return self.delay_between_batches > 0
    
    def get_delay(self) -> float:
        """获取批次间延迟"""
        return self.delay_between_batches
    
    def get_adjusted_worker_count(self, requested: int = None) -> int:
        """获取调整后的工作线程数"""
        if requested is None:
            requested = self.worker_count
        if requested <= 0:
            return self.max_workers
        return min(requested, self.max_workers) if self.worker_count == 0 else min(requested, self.worker_count)


@dataclass
class ResourceLimit:
    """资源限制配置"""
    max_cpu_percent: float = 80.0
    max_memory_percent: float = 85.0
    max_memory_mb: int = 3500
    max_disk_percent: float = 90.0
    min_free_space_gb: float = 10.0


class ResourceMonitor:
    """
    统一的系统资源监控器
    
    【单例模式】确保整个系统只有一个资源监控实例
    
    【核心功能】
    1. 收集系统资源快照
    2. 检查资源是否满足任务运行条件
    3. 动态调整线程池大小
    4. 触发资源告警
    
    【使用示例】
    ```python
    from A06_Resource import ResourceMonitor
    
    # 获取监控器实例
    monitor = ResourceMonitor.get_instance()
    
    # 检查资源
    can_run, reason = monitor.check_resource_available(required_memory_mb=500)
    if can_run:
        # 执行任务
        pass
    
    # 获取优化的工作线程数
    worker_count = monitor.get_optimal_worker_count()
    ```
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self._logger = get_logger('ResourceMonitor')
        
        # 尝试导入 psutil
        try:
            import psutil
            self._psutil = psutil
        except ImportError:
            self._psutil = None
            self._logger.warning("psutil 未安装，资源监控功能将受限")
        
        # 获取配置
        try:
            self._config = Config.get_instance()
        except Exception:
            self._config = None
        
        # 资源限制配置（使用本地 HardwareConfig）
        self._limits = ResourceLimit(
            max_cpu_percent=HardwareConfig.CPU_THRESHOLD * 100,
            max_memory_percent=HardwareConfig.MEMORY_THRESHOLD * 100,
            max_memory_mb=HardwareConfig.MAX_MEMORY_MB,
        )
        
        # 历史记录
        self._history: deque = deque(maxlen=1000)
        
        # 告警回调
        self._alert_callbacks: List[Callable] = []
        
        # 监控状态
        self._is_monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_interval = 5
        
        # 当前快照
        self._current_snapshot: Optional[ResourceSnapshot] = None
        self._snapshot_lock = threading.Lock()
        
        # CPU 核心数
        try:
            self._cpu_count = self._psutil.cpu_count() if self._psutil else 1
        except Exception:
            self._cpu_count = 1
        
        self._logger.info(f"资源监控器初始化完成 (CPU: {self._cpu_count} 核)")

    @classmethod
    def get_instance(cls) -> 'ResourceMonitor':
        """获取资源监控器单例"""
        return cls()

    def get_limits(self) -> ResourceLimit:
        """获取资源限制配置"""
        return self._limits

    def register_alert_callback(self, callback: Callable) -> None:
        """
        注册告警回调函数
        
        参数:
            callback: 告警回调函数，接收告警列表作为参数
        """
        if callback not in self._alert_callbacks:
            self._alert_callbacks.append(callback)
    
    def unregister_alert_callback(self, callback: Callable) -> None:
        """注销告警回调函数"""
        if callback in self._alert_callbacks:
            self._alert_callbacks.remove(callback)

    def collect_snapshot(self) -> ResourceSnapshot:
        """
        收集当前资源快照
        
        包含 CPU、内存、磁盘使用情况
        
        返回:
            ResourceSnapshot: 资源快照对象
        """
        try:
            if not self._psutil:
                # psutil 不可用时，返回一个默认的合理快照
                # 使用 HardwareConfig 中的配置作为默认值
                default_memory = HardwareConfig.TOTAL_MEMORY_MB * 1024 * 1024
                return ResourceSnapshot(
                    timestamp=datetime.now(),
                    cpu_percent=0.0,
                    memory_total=default_memory,
                    memory_available=default_memory,  # 假设全部可用
                    memory_percent=0.0,
                    disk_usage={}
                )

            # CPU 使用率
            cpu_percent = self._psutil.cpu_percent(interval=0.1)
            
            # 内存信息
            mem = self._psutil.virtual_memory()
            
            # 磁盘使用情况
            disk_usage = {}
            try:
                for partition in self._psutil.disk_partitions():
                    try:
                        usage = self._psutil.disk_usage(partition.mountpoint)
                        disk_usage[partition.mountpoint] = {
                            'total': usage.total,
                            'used': usage.used,
                            'free': usage.free,
                            'percent': usage.percent
                        }
                    except Exception:
                        pass
            except Exception:
                pass

            # 创建快照
            snapshot = ResourceSnapshot(
                timestamp=datetime.now(),
                cpu_percent=cpu_percent,
                memory_total=mem.total,
                memory_available=mem.available,
                memory_percent=mem.percent,
                disk_usage=disk_usage
            )

            # 更新当前快照
            with self._snapshot_lock:
                self._current_snapshot = snapshot

            # 添加到历史
            self._history.append(snapshot)
            
            return snapshot

        except Exception as e:
            self._logger.error(f"收集资源快照失败: {e}")
            return ResourceSnapshot(timestamp=datetime.now())

    def get_current_snapshot(self) -> Optional[ResourceSnapshot]:
        """获取当前资源快照"""
        with self._snapshot_lock:
            return self._current_snapshot

    def get_average_usage(self, minutes: int = 10) -> Dict[str, float]:
        """
        获取指定时间段内的平均资源使用率
        
        参数:
            minutes: 时间段（分钟）
            
        返回:
            Dict[str, float]: 平均 CPU 和内存使用率
        """
        cutoff = datetime.now() - timedelta(minutes=minutes)
        history = [s for s in self._history if s.timestamp >= cutoff]
        
        if not history:
            return {'cpu_percent': 0.0, 'memory_percent': 0.0}
        
        count = len(history)
        return {
            'cpu_percent': sum(s.cpu_percent for s in history) / count,
            'memory_percent': sum(s.memory_percent for s in history) / count,
        }

    def check_resource_available(self, required_memory_mb: int = 100, 
                                 required_disk_gb: float = 1.0) -> Tuple[bool, str]:
        """
        检查系统资源是否满足任务运行条件
        
        这是其他模块应该调用的核心方法，用于在执行任务前检查资源状态
        
        参数:
            required_memory_mb: 任务所需的最小内存（MB）
            required_disk_gb: 任务所需的最小磁盘空间（GB）
            
        返回:
            Tuple[bool, str]: (是否可以运行, 原因说明)
        """
        snapshot = self.collect_snapshot()
        
        # 检查可用内存
        available_mb = snapshot.get_available_memory_mb()
        if available_mb < required_memory_mb:
            return False, f"内存不足: 需要 {required_memory_mb}MB, 可用 {available_mb:.0f}MB"
        
        # 检查内存使用率
        if snapshot.memory_percent > self._limits.max_memory_percent:
            return False, f"内存使用率过高: {snapshot.memory_percent:.1f}% > {self._limits.max_memory_percent}%"
        
        # 检查 CPU 使用率
        if snapshot.cpu_percent > self._limits.max_cpu_percent:
            return False, f"CPU 使用率过高: {snapshot.cpu_percent:.1f}% > {self._limits.max_cpu_percent}%"
        
        # 检查磁盘空间
        if required_disk_gb > 0:
            target_disk = self._find_target_disk()
            if target_disk:
                free_gb = target_disk.get('free', 0) / (1024 ** 3)
                if free_gb < required_disk_gb:
                    return False, f"磁盘空间不足: 需要 {required_disk_gb:.1f}GB, 可用 {free_gb:.1f}GB"
        
        return True, "资源充足"

    def check_resource_available_for_task(self, task_name: str, 
                                          priority: TaskPriority = TaskPriority.NORMAL) -> Tuple[bool, str]:
        """
        检查特定任务的资源可用性
        
        参数:
            task_name: 任务名称
            priority: 任务优先级
            
        返回:
            Tuple[bool, str]: (是否可以运行, 原因说明)
        """
        # 根据任务优先级调整内存要求
        memory_requirements = {
            TaskPriority.HIGH: 200,    # 高优先级任务需要最少 200MB
            TaskPriority.NORMAL: 100, # 普通优先级需要 100MB
            TaskPriority.LOW: 50,     # 低优先级需要 50MB
        }
        
        required_memory = memory_requirements.get(priority, 100)
        return self.check_resource_available(required_memory_mb=required_memory)

    def get_alerts(self) -> List[Dict[str, Any]]:
        """
        获取当前告警列表
        
        返回:
            List[Dict[str, Any]]: 告警列表
        """
        snapshot = self.collect_snapshot()
        alerts = []
        
        # CPU 告警
        if snapshot.cpu_percent > self._limits.max_cpu_percent:
            alerts.append({
                'level': 'critical',
                'type': ResourceType.CPU,
                'message': f"CPU 使用率过高: {snapshot.cpu_percent:.1f}%",
                'threshold': self._limits.max_cpu_percent
            })
        
        # 内存告警
        if snapshot.memory_percent > self._limits.max_memory_percent:
            alerts.append({
                'level': 'critical',
                'type': ResourceType.MEMORY,
                'message': f"内存使用率过高: {snapshot.memory_percent:.1f}%",
                'threshold': self._limits.max_memory_percent
            })
        
        # 磁盘告警
        for mountpoint, usage in snapshot.disk_usage.items():
            if usage['percent'] > 90:
                alerts.append({
                    'level': 'warning',
                    'type': ResourceType.DISK,
                    'message': f"磁盘 {mountpoint} 使用率过高: {usage['percent']:.1f}%",
                    'threshold': 90
                })
        
        return alerts

    def _find_target_disk(self) -> Optional[Dict[str, Any]]:
        """查找目标磁盘（通常是数据目录所在的磁盘）"""
        try:
            if self._psutil:
                # 优先检查 /source 或 /volume1 所在的磁盘
                for partition in self._psutil.disk_partitions():
                    if '/volume1' in partition.mountpoint or '/source' in partition.mountpoint:
                        try:
                            usage = self._psutil.disk_usage(partition.mountpoint)
                            # 返回字典格式
                            return {
                                'total': usage.total,
                                'used': usage.used,
                                'free': usage.free,
                                'percent': usage.percent
                            }
                        except Exception:
                            pass
                
                # 回退到根分区
                usage = self._psutil.disk_usage('/')
                return {
                    'total': usage.total,
                    'used': usage.used,
                    'free': usage.free,
                    'percent': usage.percent
                }
        except Exception:
            pass
        
        return None

    def start_monitoring(self) -> None:
        """启动后台监控线程"""
        if self._is_monitoring:
            return
        
        self._is_monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, 
            daemon=True,
            name="ResourceMonitor-Thread"
        )
        self._monitor_thread.start()
        self._logger.info("资源监控已启动")

    def stop_monitoring(self) -> None:
        """停止后台监控"""
        self._is_monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=10)
        self._logger.info("资源监控已停止")

    def _monitor_loop(self) -> None:
        """监控循环（在后台线程中运行）"""
        while self._is_monitoring:
            try:
                # 收集快照
                self.collect_snapshot()
                
                # 检查告警
                alerts = self.get_alerts()
                if alerts:
                    for callback in self._alert_callbacks:
                        try:
                            callback(alerts)
                        except Exception as e:
                            self._logger.error(f"告警回调执行失败: {e}")
                
                # 等待下一次检查
                time.sleep(self._monitor_interval)
                
            except Exception as e:
                self._logger.error(f"监控循环异常: {e}")
                time.sleep(self._monitor_interval)

    def get_status_summary(self) -> Dict[str, Any]:
        """
        获取资源状态摘要
        
        用于在日志或通知中显示当前系统资源状态
        
        返回:
            Dict[str, Any]: 资源状态摘要
        """
        snapshot = self.collect_snapshot()
        avg_usage = self.get_average_usage()
        
        return {
            'timestamp': datetime.now().isoformat(),
            'is_monitoring': self._is_monitoring,
            'current': {
                'cpu_percent': snapshot.cpu_percent,
                'memory_percent': snapshot.memory_percent,
                'memory_available_mb': snapshot.get_available_memory_mb(),
            },
            'average_10min': avg_usage,
            'limits': {
                'max_cpu_percent': self._limits.max_cpu_percent,
                'max_memory_percent': self._limits.max_memory_percent,
                'max_memory_mb': self._limits.max_memory_mb,
            },
        }

    def get_optimal_worker_count(self) -> int:
        """
        根据当前资源状态获取最优工作线程数
        
        这是任务调度器应该调用的核心方法
        
        返回:
            int: 推荐的工作线程数
        """
        snapshot = self.collect_snapshot()
        cpu_percent = snapshot.cpu_percent
        
        # 根据 CPU 使用率动态调整线程数
        if cpu_percent < 30:
            # CPU 空闲，可以增加线程
            return min(self._cpu_count, 4)
        elif cpu_percent < 50:
            # CPU 轻度使用
            return min(self._cpu_count, 2)
        elif cpu_percent < 70:
            # CPU 中度使用
            return 1
        else:
            # CPU 高负载，只用 1 个线程或暂停
            return 1

    def get_execution_strategy(self, task_name: str = None,
                               required_memory_mb: int = 100,
                               required_disk_gb: float = 1.0) -> Tuple[ResourceLevel, ExecutionStrategy, str]:
        """
        【核心方法】获取任务执行策略
        
        与 check_resource_available 不同，这个方法在资源不足时返回降级策略而不是跳过任务。
        资源不足时自动调整：
        - 并行 → 串行
        - 快速 → 慢速
        
        参数:
            task_name: 任务名称（用于日志）
            required_memory_mb: 任务需要的最小内存
            required_disk_gb: 任务需要的最小磁盘空间
            
        返回:
            Tuple[ResourceLevel, ExecutionStrategy, str]: (资源等级, 执行策略, 原因)
        """
        snapshot = self.collect_snapshot()
        
        # 检查资源是否足够
        available_mb = snapshot.get_available_memory_mb()
        resource_ok = True
        warnings = []
        
        # 检查内存
        if available_mb < required_memory_mb:
            resource_ok = False
            warnings.append(f"内存偏低: {available_mb:.0f}MB < {required_memory_mb}MB")
        elif snapshot.memory_percent > 80:
            warnings.append(f"内存使用率高: {snapshot.memory_percent:.1f}%")
        
        # 检查 CPU
        if snapshot.cpu_percent > 85:
            warnings.append(f"CPU 使用率高: {snapshot.cpu_percent:.1f}%")
        
        # 根据资源状态确定等级和策略
        if resource_ok and snapshot.cpu_percent < 85 and snapshot.memory_percent < 80:
            # 资源充足
            strategy = ExecutionStrategy(
                level=ResourceLevel.NORMAL,
                worker_count=self._cpu_count,
                max_workers=HardwareConfig.MAX_WORKERS,
                batch_size=HardwareConfig.BATCH_SIZE,
                delay_between_batches=0.0,
            )
            return ResourceLevel.NORMAL, strategy, "资源充足，正常运行"
        
        elif snapshot.cpu_percent > 90 or snapshot.memory_percent > 90:
            # 资源紧张 - 极限降级
            strategy = ExecutionStrategy(
                level=ResourceLevel.CRITICAL,
                worker_count=1,
                max_workers=1,
                batch_size=50,
                delay_between_batches=1.0,
                enable_serial_only=True,
                enable_throttle=True,
            )
            reason = f"资源紧张: CPU {snapshot.cpu_percent:.1f}%, 内存 {snapshot.memory_percent:.1f}%"
            self._logger.warning(f"[{task_name}] {reason}, 切换为串行模式")
            return ResourceLevel.CRITICAL, strategy, reason
        
        else:
            # 资源偏低 - 适度降级
            strategy = ExecutionStrategy(
                level=ResourceLevel.LOW,
                worker_count=max(1, self._cpu_count // 2),
                max_workers=max(1, HardwareConfig.MAX_WORKERS // 2),
                batch_size=HardwareConfig.BATCH_SIZE // 2,
                delay_between_batches=0.3,
            )
            reason = f"资源偏低: CPU {snapshot.cpu_percent:.1f}%, 内存 {snapshot.memory_percent:.1f}%"
            self._logger.info(f"[{task_name}] {reason}, 降级处理")
            return ResourceLevel.LOW, strategy, reason

    def get_optimal_worker_count_for_task(self, task_type: str) -> int:
        """
        根据任务类型获取最优工作线程数
        
        参数:
            task_type: 任务类型 (scan/dedup/compress/clean)
            
        返回:
            int: 推荐的工作线程数
        """
        # 不同任务类型的线程需求
        task_worker_config = {
            'scan': 2,       # 扫描任务可以用更多线程
            'dedup': 2,     # 去重任务中等
            'compress': 1,   # 压缩任务 CPU 密集，用少线程
            'clean': 2,     # 清洗任务中等
            'hash': 2,      # 哈希计算 IO 为主
        }
        
        base_workers = task_worker_config.get(task_type, 2)
        return min(base_workers, self.get_optimal_worker_count())

    def format_size(self, bytes_size: int) -> str:
        """
        格式化字节大小为人类可读格式
        
        参数:
            bytes_size: 字节大小
            
        返回:
            str: 格式化后的大小字符串
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} PB"

    def format_resource_status(self) -> str:
        """
        格式化资源状态为可读字符串
        
        用于日志输出和通知消息
        
        返回:
            str: 格式化的资源状态字符串
        """
        snapshot = self.collect_snapshot()
        
        lines = [
            "=== 系统资源状态 ===",
            f"CPU: {snapshot.cpu_percent:.1f}%",
            f"内存: {snapshot.memory_percent:.1f}% (可用: {snapshot.get_available_memory_mb():.0f}MB)",
        ]
        
        for mountpoint, usage in snapshot.disk_usage.items():
            lines.append(f"磁盘 {mountpoint}: {usage['percent']:.1f}% (可用: {self.format_size(usage['free'])})")
        
        return "\n".join(lines)


class TaskSchedulerHelper:
    """
    任务调度辅助类
    
    帮助任务脚本在执行前检查资源状态，获取执行策略（降级模式而不是跳过）
    
    【核心设计】
    - 资源充足 → 正常并行、快速处理
    - 资源不足 → 改为串行、降低速度，确保任务完成
    
    【使用示例】
    ```python
    from A06_Resource import TaskSchedulerHelper, ResourceLevel
    
    helper = TaskSchedulerHelper()
    
    # 获取执行策略（不会跳过任务）
    level, strategy, reason = helper.get_execution_strategy(
        task_name="我的任务",
        required_memory_mb=500
    )
    
    print(f"资源等级: {level.value}")
    print(f"原因: {reason}")
    print(f"使用线程数: {strategy.get_adjusted_worker_count()}")
    print(f"批次大小: {strategy.get_adjusted_batch_size(200)}")
    
    if strategy.should_add_delay():
        print(f"批次间延迟: {strategy.get_delay()}秒")
    ```
    """

    def __init__(self, monitor: Optional[ResourceMonitor] = None):
        """
        初始化任务调度辅助类
        
        参数:
            monitor: 资源监控器实例，None 则使用单例
        """
        self._monitor = monitor or ResourceMonitor.get_instance()
        self._cooldown_seconds = 60
        self._last_check_time = 0
        self._logger = get_logger('TaskSchedulerHelper')

    def can_run_task(self, required_memory_mb: int = 100) -> Tuple[bool, str]:
        """
        检查是否可以运行任务（兼容旧接口）
        
        【注意】此方法保留以兼容旧代码，但推荐使用 get_execution_strategy
        
        参数:
            required_memory_mb: 任务需要的最小内存（MB）
            
        返回:
            Tuple[bool, str]: (是否可以运行, 原因)
        """
        current_time = time.time()
        
        # 冷却检查（避免频繁检查）
        if current_time - self._last_check_time < 5:
            return True, "冷却中，跳过检查"
        
        self._last_check_time = current_time
        
        # 检查资源
        available, message = self._monitor.check_resource_available(required_memory_mb)
        
        if not available:
            self._logger.warning(f"资源不足: {message}")
            return False, message
        
        return True, "资源充足"

    def get_execution_strategy(self, task_name: str = "Task",
                              required_memory_mb: int = 100) -> Tuple[ResourceLevel, ExecutionStrategy, str]:
        """
        【推荐方法】获取任务执行策略
        
        资源不足时返回降级策略而不是跳过任务：
        - 以前可以并行的 → 改为串行
        - 以前快速处理的 → 降低速度
        
        参数:
            task_name: 任务名称（用于日志）
            required_memory_mb: 任务需要的最小内存（MB）
            
        返回:
            Tuple[ResourceLevel, ExecutionStrategy, str]: (资源等级, 执行策略, 原因)
        """
        return self._monitor.get_execution_strategy(
            task_name=task_name,
            required_memory_mb=required_memory_mb
        )

    def can_run_task_by_priority(self, task_name: str, 
                                 priority: TaskPriority = TaskPriority.NORMAL) -> Tuple[bool, str]:
        """
        根据优先级检查是否可以运行任务
        
        参数:
            task_name: 任务名称（用于日志）
            priority: 任务优先级
            
        返回:
            Tuple[bool, str]: (是否可以运行, 原因)
        """
        return self._monitor.check_resource_available_for_task(task_name, priority)

    def get_optimal_worker_count(self) -> int:
        """
        获取当前最优工作线程数
        
        返回:
            int: 推荐的工作线程数
        """
        return self._monitor.get_optimal_worker_count()

    def wait_for_resources(self, required_memory_mb: int = 100,
                           max_wait_seconds: int = 300) -> Tuple[bool, str]:
        """
        等待资源满足条件
        
        参数:
            required_memory_mb: 需要的内存（MB）
            max_wait_seconds: 最大等待时间（秒）
            
        返回:
            Tuple[bool, str]: (是否等到资源, 原因)
        """
        start_time = time.time()
        check_interval = 10  # 每 10 秒检查一次
        
        while time.time() - start_time < max_wait_seconds:
            can_run, reason = self.can_run_task(required_memory_mb)
            
            if can_run:
                self._logger.info(f"资源已就绪: {reason}")
                return True, reason
            
            self._logger.info(f"等待资源... ({reason}), 剩余 {max_wait_seconds - (time.time() - start_time):.0f}秒")
            time.sleep(check_interval)
        
        return False, f"等待超时 ({max_wait_seconds}秒)"

    def should_pause_tasks(self) -> Tuple[bool, str]:
        """
        检查是否应该暂停任务
        
        当系统资源紧张时，建议暂停低优先级任务
        
        返回:
            Tuple[bool, str]: (是否应该暂停, 原因)
        """
        snapshot = self._monitor.collect_snapshot()
        
        # 高资源紧张度阈值
        HIGH_CPU_THRESHOLD = 90
        HIGH_MEMORY_THRESHOLD = 90
        
        if snapshot.cpu_percent > HIGH_CPU_THRESHOLD:
            return True, f"CPU 过高: {snapshot.cpu_percent:.1f}%"
        
        if snapshot.memory_percent > HIGH_MEMORY_THRESHOLD:
            return True, f"内存过高: {snapshot.memory_percent:.1f}%"
        
        return False, "资源正常"


if __name__ == "__main__":
    print("=== 测试资源监控 ===")
    print()
    
    # 获取监控器
    monitor = ResourceMonitor.get_instance()
    
    # 获取资源状态
    snapshot = monitor.collect_snapshot()
    print(f"【当前资源状态】")
    print(f"  CPU 使用率: {snapshot.cpu_percent}%")
    print(f"  内存使用率: {snapshot.memory_percent}%")
    print(f"  可用内存: {monitor.format_size(snapshot.memory_available)}")
    
    # 获取告警
    alerts = monitor.get_alerts()
    if alerts:
        print(f"\n【告警】")
        for alert in alerts:
            print(f"  - {alert['message']}")
    
    # 检查资源
    can_run, reason = monitor.check_resource_available(required_memory_mb=500)
    print(f"\n【资源检查】")
    print(f"  可运行任务: {'是' if can_run else '否'}")
    print(f"  原因: {reason}")
    
    # 获取推荐线程数
    workers = monitor.get_optimal_worker_count()
    print(f"\n【推荐工作线程数】: {workers}")
    
    # 获取格式化的状态
    print(f"\n【格式化状态】")
    print(monitor.format_resource_status())
    
    # 测试调度辅助
    print(f"\n【任务调度辅助测试】")
    helper = TaskSchedulerHelper()
    can_run, reason = helper.can_run_task(required_memory_mb=100)
    print(f"  任务可执行: {'是' if can_run else '否'} - {reason}")
    
    # 【新增】测试执行策略
    print(f"\n【执行策略测试】")
    level, strategy, reason = helper.get_execution_strategy(
        task_name="测试任务",
        required_memory_mb=100
    )
    print(f"  资源等级: {level.value}")
    print(f"  原因: {reason}")
    print(f"  工作线程数: {strategy.get_adjusted_worker_count(4)}")
    print(f"  批次大小: {strategy.get_adjusted_batch_size(200)}")
    print(f"  串行模式: {'是' if strategy.is_serial_only() else '否'}")
    if strategy.should_add_delay():
        print(f"  批次间延迟: {strategy.get_delay()}秒")
    
    print("\n资源监控测试完成!")
