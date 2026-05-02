# -*- coding: utf-8 -*-
"""
青龙面板文件管理系统 - 正式字典管理模块
基于群晖 DS918+ (DSM 7.3) 优化的文件管理自动化系统

=== A12_Formal.py 模块功能和中文注释 开始 ===

功能说明：
- 调用文件夹配置管理 A01_Config.py 中的正式字典文件夹
- 记录 dict_type = "formal" 到 DaTa.db
- 后续运行使用比较模式，确保字典准确性
- 日志保留一次
- 导出 JSON 到 /A5_Json/B01_Formal_<文件夹编号>.json
- 数据库备份功能已禁用

=== A12_Formal.py 模块功能和中文注释 结束 ===
"""

import os
import json
import shutil
import threading
from datetime import datetime
from pathlib import Path


def get_logger(name: str):
    """获取日志记录器"""
    try:
        from A01_Config import Config
        config = Config.get_instance()
        return config.get_logger(name)
    except Exception:
        import logging
        logging.basicConfig(level=logging.INFO, format='[%(asctime)s] [%(levelname)s] %(message)s')
        return logging.getLogger(name)


class FormalManager:
    """
    正式字典管理器
    
    功能：
    1. 调用 A01_Config.py 中的正式字典文件夹
    2. 记录 dict_type = "formal" 到 DaTa.db
    3. 后续运行使用比较模式，确保字典准确性
    4. 日志保留一次
    5. 导出 JSON 到 /A5_Json/B01_Formal_<文件夹编号>.json
    6. 数据库备份功能已禁用
    """
    
    _instance = None
    _db_lock = threading.RLock()  # 数据库访问锁，防止并发写入导致锁定
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._logger = None
        self._resource_monitor = None
        self._scheduler_helper = None
        self._to_synology_path = None
        self._project_dir = ''
        self._data_dir = ''
        self._log_dir = ''
        self._json_dir = ''
        self._db_path = ''
        self._formal_folders = {}
        
        try:
            self._logger = get_logger('A12_Formal')
            
            # 初始化路径转换函数
            try:
                from A03_Logger import container_to_synology
                self._to_synology_path = container_to_synology
            except Exception:
                def _simple_convert(path: str) -> str:
                    if '/source/' in path:
                        return path.replace('/source/', '/volume1/')
                    elif path.startswith('/source'):
                        return path.replace('/source', '/volume1', 1)
                    return path
                self._to_synology_path = _simple_convert
            
            # 初始化路径
            self._init_paths()
            
            # 初始化数据库（带重试机制）
            self._init_database_with_retry()
            
            # === 初始化 A06_Resource 资源监控器 ===
            self._init_resource_monitor()
            
            self._logger.info("正式字典管理器初始化完成")
        except Exception:
            self._initialized = False
            raise
    
    def _init_paths(self) -> None:
        """初始化路径"""
        script_dir = os.path.dirname(os.path.dirname(__file__))
        self._project_dir = script_dir
        self._data_dir = os.path.join(script_dir, 'A3_Data')
        self._log_dir = os.path.join(script_dir, 'A4_Logs')
        self._json_dir = os.path.join(script_dir, 'A5_Json')
        self._db_path = os.path.join(self._data_dir, 'DaTa.db')
        
        # 从 A01_Config 获取正式字典文件夹配置
        try:
            config_path = os.path.join(script_dir, 'A1_Config', 'A01_Config.py')
            if os.path.exists(config_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("A01_Config", config_path)
                if spec and spec.loader:
                    config_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(config_module)
                    self._formal_folders = getattr(config_module, 'FORMAL_FOLDERS', {})
                    self._logger.info(f"已加载 FORMAL_FOLDERS，共 {len(self._formal_folders)} 个文件夹")
                else:
                    self._logger.warning("无法加载 A01_Config.py，使用默认配置")
                    self._formal_folders = {}
            else:
                self._logger.warning("未找到 A01_Config.py，使用默认配置")
                self._formal_folders = {}
        except Exception as e:
            self._logger.warning(f"无法加载 FORMAL_FOLDERS: {e}")
            self._formal_folders = {}
        
        # 确保目录存在
        for dir_path in [self._data_dir, self._log_dir, self._json_dir]:
            os.makedirs(dir_path, exist_ok=True)
    
    def _init_database(self) -> None:
        """初始化数据库"""
        try:
            from A07_Database import get_database
            get_database()
            self._logger.info("数据库初始化完成（使用 DatabaseManager）")
        except Exception as e:
            self._logger.error(f"数据库初始化失败: {e}")
            raise
    
    def _init_database_with_retry(self, max_retries: int = 3, retry_delay: int = 2) -> None:
        """带重试机制的数据库初始化"""
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                self._init_database()
                return
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    self._logger.warning(f"数据库初始化失败（第 {attempt}/{max_retries} 次）: {e}，{retry_delay} 秒后重试...")
                    import time
                    time.sleep(retry_delay)
                else:
                    self._logger.error(f"数据库初始化最终失败: {e}")
                    raise last_error
    
    def _init_resource_monitor(self):
        """初始化资源监控器"""
        try:
            from A06_Resource import ResourceMonitor, TaskSchedulerHelper
            self._resource_monitor = ResourceMonitor.get_instance()
            self._scheduler_helper = TaskSchedulerHelper(self._resource_monitor)
            self._logger.info("正式字典管理器已连接统一资源监控系统")
        except Exception:
            self._logger.warning("资源监控模块不可用")
            self._scheduler_helper = None

    def _check_resources_before_task(self, folder_id: str) -> tuple:
        """任务执行前检查资源"""
        if self._scheduler_helper is None:
            return True, ""
        can_run, reason = self._scheduler_helper.can_run_task(required_memory_mb=100)
        if not can_run:
            self._logger.warning(f"资源检查不通过 [{folder_id}]: {reason}")
            return False, reason
        return True, "资源充足"

    def _log_resource_status(self, context: str = ""):
        """记录当前资源状态"""
        if self._resource_monitor is None:
            return
        try:
            status = self._resource_monitor.format_resource_status()
            if context:
                self._logger.info(f"{context} - {status}")
            else:
                self._logger.debug(status)
        except Exception as e:
            self._logger.warning(f"获取资源状态失败: {e}")

    @classmethod
    def get_instance(cls):
        return cls()
    
    def _is_first_run(self, folder_id: str) -> bool:
        """检查是否是首次运行"""
        try:
            from A07_Database import get_database
            db = get_database()
            result = db.query_one(
                'SELECT COUNT(*) FROM file_metadata WHERE folder_id = %s AND dict_type = %s',
                (folder_id, 'formal')
            )
            count = result[0] if result else 0
            return count == 0
        except Exception:
            return True
    
    def backup_database(self) -> bool:
        """备份数据库（已禁用）"""
        return True
    
    def scan_folder(self, folder_id: str) -> dict:
        """扫描单个正式字典文件夹"""
        try:
            from A03_Logger import get_logger, log_folder_scan
        except Exception:
            from A01_Config import get_logger
            log_folder_scan = lambda *args, **kwargs: None
        
        folder_logger = get_logger(f"B01_Formal_{folder_id}")
        
        try:
            log_folder_scan(f"B01_Formal_{folder_id}", folder_id, self._formal_folders, "开始扫描")
        except Exception:
            pass
        
        os.makedirs(self._log_dir, exist_ok=True)
        
        result = {
            'folder_id': folder_id,
            'folder_path': self._formal_folders.get(folder_id, ''),
            'files': [],
            'total_files': 0,
            'total_words': 0,
            'success': True,
            'error': None
        }
        
        folder_path = self._formal_folders.get(folder_id)
        if not folder_path:
            result['success'] = False
            result['error'] = f"文件夹编号 {folder_id} 不存在"
            return result
        
        if not os.path.exists(folder_path):
            result['success'] = False
            result['error'] = f"文件夹路径不存在: {folder_path}"
            return result
        
        try:
            try:
                from A01_Config import EXCLUDE_EXTENSIONS, EXCLUDE_FILENAMES, EXCLUDE_DIRNAMES
            except Exception:
                EXCLUDE_EXTENSIONS = {'.pyc', '.pyo', '.pyd'}
                EXCLUDE_FILENAMES = set()
                EXCLUDE_DIRNAMES = {'__pycache__', '.git', '.svn'}
            
            processed = 0
            for root, dirs, files in os.walk(folder_path):
                dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRNAMES 
                          and not d.startswith('.') and '#recycle' not in d]
                
                for filename in files:
                    processed += 1
                    if processed % 100 == 0:
                        folder_logger.info(f"扫描进度: 已处理 {processed} 个文件...")
                    
                    if filename in EXCLUDE_FILENAMES:
                        continue
                    ext = os.path.splitext(filename)[1].lower()
                    if ext in EXCLUDE_EXTENSIONS:
                        continue
                    if '$★$☆と封面と' in filename:
                        continue
                    
                    file_path = os.path.join(root, filename)
                    try:
                        file_hash = self._calculate_file_hash(file_path)
                        
                        words = set()
                        if filename.endswith('.txt'):
                            try:
                                with open(file_path, 'r', encoding='utf-8') as f:
                                    for line in f:
                                        word = line.strip()
                                        if word and not word.startswith('#'):
                                            words.add(word)
                            except Exception:
                                pass
                        
                        file_info = {
                            'file_path': file_path,
                            'file_name': filename,
                            'file_size': os.path.getsize(file_path),
                            'file_hash': file_hash,
                            'word_count': len(words),
                            'words': list(words)
                        }
                        result['files'].append(file_info)
                        result['total_words'] += len(words)
                        
                    except Exception as e:
                        folder_logger.error(f"处理文件失败 {self._to_synology_path(file_path)}: {e}")
            
            result['total_files'] = len(result['files'])
            
            try:
                log_folder_scan(f"B01_Formal_{folder_id}", folder_id, self._formal_folders,
                               f"扫描完成: {result['total_files']} 个文件, {result['total_words']} 个词条")
            except Exception:
                pass
            
        except Exception as e:
            result['success'] = False
            result['error'] = str(e)
        
        return result
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """计算文件哈希"""
        import hashlib
        try:
            md5 = hashlib.md5()
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    md5.update(chunk)
            return md5.hexdigest()
        except Exception:
            return ''
    
    def save_to_database(self, folder_id: str, scan_result: dict) -> bool:
        """保存扫描结果到数据库"""
        if not scan_result.get('success'):
            return False

        try:
            from A07_Database import get_database
            db = get_database()

            db.execute_and_commit('DELETE FROM file_metadata WHERE folder_id = %s AND dict_type = %s', (folder_id, 'formal'))

            files = scan_result.get('files', [])
            if files:
                batch_data = []
                for file_info in files:
                    batch_data.append((
                        'formal',
                        folder_id,
                        file_info['file_path'],
                        file_info['file_name'],
                        file_info['file_size'],
                        file_info['file_hash'],
                        file_info.get('word_count', 0),
                        int(os.path.getmtime(file_info['file_path'])) if os.path.exists(file_info['file_path']) else 0
                    ))

                db.executemany_and_commit('''
                    INSERT INTO file_metadata 
                    (dict_type, folder_id, file_path, file_name, file_size, file_hash, word_count, modified_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', batch_data)

            self._logger.info(f"已保存文件夹 {folder_id} 的 {len(files)} 条记录到数据库 (dict_type=formal)")
            return True

        except Exception as e:
            self._logger.error(f"保存到数据库失败: {e}")
            return False
    
    def compare_with_database(self, folder_id: str, scan_result: dict) -> tuple:
        """与数据库中的记录比较"""
        added = []
        removed = []
        
        try:
            from A07_Database import get_database
            db = get_database()
            
            rows = db.query_all('''
                SELECT file_path, file_name, file_hash, word_count 
                FROM file_metadata 
                WHERE folder_id = %s AND dict_type = 'formal' AND deleted_at IS NULL
            ''', (folder_id,))
            db_records = {row[0]: {'file_name': row[1], 'file_hash': row[2], 'word_count': row[3]} 
                          for row in rows}
            
            scan_files = {f['file_path']: f for f in scan_result.get('files', [])}
            
            for file_path, file_info in scan_files.items():
                if file_path not in db_records:
                    added.append(file_info)
                elif db_records[file_path]['file_hash'] != file_info['file_hash']:
                    added.append(file_info)
            
            for file_path, db_info in db_records.items():
                if file_path not in scan_files:
                    removed.append(db_info)
            
            has_changes = len(added) > 0 or len(removed) > 0
            return has_changes, added, removed
            
        except Exception as e:
            self._logger.error(f"比较失败: {e}")
            return True, [], []
    
    def rotate_log(self, folder_id: str) -> None:
        """日志保留一次"""
        try:
            log_file = os.path.join(self._log_dir, f'B01_Formal_{folder_id}.log')
            log_backup = os.path.join(self._log_dir, f'B01_Formal_{folder_id}.log.old')
            
            if os.path.exists(log_file):
                if os.path.exists(log_backup):
                    os.remove(log_backup)
                os.rename(log_file, log_backup)
                self._logger.info("已删除上一次的日志文件")
        except Exception as e:
            self._logger.warning(f"日志轮转失败: {e}")
    
    def export_to_json(self, folder_id: str, scan_result: dict) -> str | None:
        """导出字典到 JSON 文件"""
        try:
            export_path = os.path.join(self._json_dir, f'B01_Formal_{folder_id}.json')
            
            export_data = {
                'folder_id': folder_id,
                'dict_type': 'formal',
                'export_time': datetime.now().isoformat(),
                'total_files': scan_result.get('total_files', 0),
                'total_words': scan_result.get('total_words', 0),
                'files': []
            }
            
            for file_info in scan_result.get('files', []):
                export_data['files'].append({
                    'file_name': file_info['file_name'],
                    'file_path': file_info['file_path'],
                    'file_size': file_info['file_size'],
                    'word_count': file_info['word_count'],
                    'words': file_info.get('words', [])
                })
            
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
            
            self._logger.info(f"已导出字典到: {export_path}")
            return export_path
            
        except Exception as e:
            self._logger.error(f"导出 JSON 失败: {e}")
            return None
    
    def run(self, folder_id: str) -> dict:
        """运行正式字典任务"""
        result = {
            'folder_id': folder_id,
            'success': True,
            'message': '',
            'is_first_run': False
        }
        
        self._logger.info(f"开始处理正式字典文件夹: {folder_id}")
        
        can_proceed, reason = self._check_resources_before_task(folder_id)
        if not can_proceed:
            result['success'] = False
            result['message'] = f"资源不足，跳过任务: {reason}"
            self._logger.warning(result['message'])
            return result

        self._log_resource_status(f"开始处理文件夹 {folder_id}")
        
        is_first_run = self._is_first_run(folder_id)
        result['is_first_run'] = is_first_run
        
        self.rotate_log(folder_id)
        
        # === 数据库备份已禁用 ===
        
        scan_result = self.scan_folder(folder_id)
        if not scan_result.get('success'):
            result['success'] = False
            result['message'] = scan_result.get('error', '扫描失败')
            return result
        
        if is_first_run:
            self._logger.info("首次运行，保存到数据库")
            if not self.save_to_database(folder_id, scan_result):
                result['success'] = False
                result['message'] = '保存到数据库失败'
                return result
        else:
            has_changes, added, removed = self.compare_with_database(folder_id, scan_result)
            if has_changes:
                self._logger.info(f"检测到变化: 新增 {len(added)} 个, 删除 {len(removed)} 个")
                if not self.save_to_database(folder_id, scan_result):
                    result['success'] = False
                    result['message'] = '更新数据库失败'
                    return result
            else:
                self._logger.info("无变化，字典数据一致")
        
        export_path = self.export_to_json(folder_id, scan_result)
        if not export_path:
            self._logger.warning("导出 JSON 失败，但任务继续")
        
        result['message'] = f"处理完成: {scan_result.get('total_files', 0)} 个文件, {scan_result.get('total_words', 0)} 个词条"
        self._logger.info(result['message'])
        
        return result
    
    def run_all(self) -> list:
        """运行所有正式字典文件夹"""
        results = []
        for folder_id in sorted(self._formal_folders.keys()):
            result = self.run(folder_id)
            results.append(result)
        
        self._log_resource_status("正式字典任务全部完成")
        return results
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        try:
            from A07_Database import get_database
            db = get_database()
            
            row = db.query_one("SELECT COUNT(*), SUM(word_count) FROM file_metadata WHERE dict_type = 'formal'")
            
            return {
                'total_records': row[0] if row else 0,
                'total_words': row[1] if row and row[1] else 0,
                'folder_count': len(self._formal_folders)
            }
        except Exception as e:
            self._logger.error(f"获取统计失败: {e}")
            return {'total_records': 0, 'total_words': 0, 'folder_count': 0}


if __name__ == "__main__":
    print("=== 正式字典管理模块自测试 ===")
    
    manager = FormalManager()
    
    print("\n1. 测试扫描文件夹:")
    result = manager.run('01')
    print(f"   结果: {result}")
    
    print("\n2. 统计信息:")
    stats = manager.get_stats()
    print(f"   {stats}")
    
    print("\n3. 测试运行所有文件夹:")
    all_results = manager.run_all()
    for r in all_results:
        print(f"   {r['folder_id']}: {r['message']}")
    
    print("\n正式字典管理模块测试完成!")