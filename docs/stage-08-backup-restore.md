# 数据库备份与恢复演练

## 本轮目标

Git 保存代码与文档，运行数据库另行备份。新增 app/backup.py 和 tests/test_backup.py，使用 Python 自带 SQLite，不安装依赖。
备份包含所有目标、名称、网址、暂停状态、全部检查历史、故障事件及目标编号序列，而非页面上的最近 20 条。
本轮命令不覆盖任何已有文件，不替换正在使用的数据库。先用新文件完成恢复演练。

## 三个命令

在 D:\itm4 使用项目 Python：

~~~powershell
& '.\.venv\Scripts\python.exe' -m app.backup backup
& '.\.venv\Scripts\python.exe' -m app.backup verify '备份文件完整路径'
& '.\.venv\Scripts\python.exe' -m app.backup restore '备份文件完整路径' --output '新的恢复文件完整路径'
~~~

backup 默认读取 data/xunxiaozhan.sqlite3，在 backups 生成带 UTC 时间戳的文件。
可用 --source 和 --output 明确指定源文件与新文件。相对路径按当前终端目录解释。
verify 只读核对 SQLite 完整性、当前版本所需表和字段、外键及目标编号序列，并返回统计。
restore 使用相同的 SQLite 快照过程，将备份恢复成一个新的独立数据库，校验通过后才发布文件。
成功输出 verified: true、文件路径、targets、checks、incidents、paused_targets、last_target_id。
失败输出原因并返回非零退出码；不要把“命令已运行”当成成功。

## 为什么不用直接复制正在运行的数据库

SQLite 的已提交内容可能仍在 WAL 日志中，只复制主文件可能漏数据。
工具使用 sqlite3.Connection.backup 获取一致快照，再输出不依赖 WAL/SHM 的独立数据库文件。
复制阶段超过 30 秒会中止，可减少写入后重试。完整性校验耗时不包含在此时限内。
后台可以继续运行；快照不承诺精确对应按下回车的瞬间，新生成的记录也不会自动追加到旧备份。
工具先写同目录临时文件，校验并同步后用硬链接发布；目标已存在则失败，不覆盖。
本轮在 Windows 本机磁盘验证；输出磁盘必须支持硬链接，网络共享盘与其他系统未验证。

## 恢复保护与边界

- 源文件必须存在，路径写错不会自动生成空源库。
- 源与目标不能相同，已有目标文件或其 WAL/SHM/journal 文件会阻止操作。
- 目标使用与应用相同的进程锁，不能同时在该路径启动应用与恢复。残留 .lock 文件本身不是故障，不要删除它解锁。
- 损坏、缺少所需表字段、外键无效或编号序列缺失的数据库拒绝发布。
- 校验不证明文件来源可信或未被人为修改；只恢复自己保存的可信备份。本轮不提供旧版本迁移、加密、签名或自动清理。
- 恢复保留备份时的暂停状态。真正将它投入使用后，未暂停目标会重新开始巡检。
- 恢复旧快照会失去备份之后的记录及编号分配；编号只保证相对于该快照的连续性，不能保证跨回滚不复用备份后分配过的编号。
- 若未来需要替换运行数据库，应先停止 8000，保留当前数据及所需日志，关闭旧页面，再单独制定替换步骤。本轮不执行替换。
- backups 和 data 已被 Git 忽略。项目同盘副本可用于演练，但不能防止整块硬盘故障；实际长期备份需要另存到可信独立介质。

## 本轮亲手演练

这些命令只生成新文件，8000 后端可保持运行：

~~~powershell
Set-Location -LiteralPath 'D:\itm4'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$backupFile = Join-Path (Get-Location) "backups\practice-$stamp.sqlite3"
$restoreFile = Join-Path (Get-Location) "data\restore-practice-$stamp.sqlite3"
& '.\.venv\Scripts\python.exe' -m app.backup backup --output $backupFile
& '.\.venv\Scripts\python.exe' -m app.backup verify $backupFile
& '.\.venv\Scripts\python.exe' -m app.backup restore $backupFile --output $restoreFile
& '.\.venv\Scripts\python.exe' -m app.backup verify $restoreFile
~~~

每条命令都应成功。比较备份和恢复副本的五项统计，应完全相同；路径和 operation 不同是正常的。
当前运行数据库还可能继续新增历史，因此不要要求它此刻的统计与旧备份相同。
恢复文件只是演练副本，应用仍使用 data/xunxiaozhan.sqlite3。
再次向同一个 restoreFile 恢复应被拒绝，已有副本仍可 verify。

理解问题：为什么 Git 提交成功，并不能证明目标名称、检查历史和故障事件也已备份？

## 验证记录

2026-10-01：8 项临时数据库自动测试通过，包括：
- 备份后修改源库，恢复副本仍保留原来的全部表记录，正常初始化后可读取。
- 暂停状态、历史、恢复事件与已删除目标的编号序列保留，新目标编号继续递增。
- 应用持有源库锁及 WAL 连接仍打开时，备份包含已提交的 WAL 记录。
- 同路径、已有文件、残留日志、目标锁占用、损坏库、缺表、无效外键及编号序列被拒绝。
- 发布时出现同名文件也不覆盖，失败后清理本工具创建的临时主文件。
- 命令行备份、校验、恢复、失败退出码及中文空格路径。
测试期间修复了 Windows 文件同步需要可写句柄的问题，未修改学习者运行数据库。
学习者于 2026-10-01 完成亲手备份、校验及恢复到新文件的演练，并提供命令输出：
- 备份：backups/practice-20261001-183458-377.sqlite3。
- 恢复副本：data/restore-practice-20261001-183458-377.sqlite3。
- 两个文件的 verify 均显示 verified 为 true。
- 五项统计一致：targets=3、checks=182、incidents=4、paused_targets=1、last_target_id=5。
恢复命令的粘贴输出不完整，但随后恢复文件的独立校验成功；本次确认的是副本校验及统计一致，
没有将副本替换为运行数据库，也没有据此宣称已逐条人工核对全部记录。
已再次通过 git check-ignore 确认运行库、恢复副本和备份文件均被 Git 忽略。
学习者回答仍将 Git 提交与 data 数据混淆，已解释：Git 保存本轮代码与文档，data 并未提交；
数据库由 backup 命令另存。本轮操作验收完成，该概念后续继续巩固。Git 提交由学习者执行，待确认。
