# 第十三阶段：容器数据库备份、导出与恢复副本

## 本轮范围

上一轮已提交为 71312c8。本轮复用已有 app.backup 工具，不修改应用代码或 Docker 配置。
学习链路：运行库 → 容器内 SQLite 快照 → Windows 备份文件 → Windows 新恢复文件。
本轮不替换当前运行库，不切换应用使用恢复副本。完成后还剩项目展示整理一轮。

## 四个动作分别证明什么

| 动作 | 作用 |
| --- | --- |
| backup | 通过 SQLite 快照接口生成独立副本，可处理源库已提交的 WAL 内容 |
| compose cp | 将已经生成的快照从容器复制到 Windows |
| verify | 校验完整性、表结构、外键及编号序列，显示五项统计；不创建备份 |
| restore | 将备份恢复到一个不存在的新文件，拒绝覆盖已有文件 |

不要直接 cp 正在使用的 /app/data/xunxiaozhan.sqlite3；运行库可能依赖 WAL，单独复制主文件可能遗漏已提交内容。
容器内 /app/backups 未挂载卷，其文件随容器删除而丢失，所以必须完成导出。
Windows backups 目录被 Git 忽略，Git 提交不包含这些数据库文件。
备份与恢复副本都放在本机仍不能防同一磁盘损坏；长期保留需另存独立介质。

## 开始前

Docker Desktop 应运行，巡小站容器应为 running。若之前停止，在本项目目录用 compose start 启动。
保持所有目标暂停，便于比较五项统计；这里暂停是为了固定演练数据，backup 本身支持后端运行时备份。
不需要启动 Windows 的 18765 测试服务。
本轮准备时只读校验为 targets=2、checks=51、incidents=3、paused_targets=2、last_target_id=2。
如果之后新增记录，以本次 backup 输出为基准，不强行凑齐旧数字。

全程在同一个 PowerShell 窗口逐段执行，变量要保留。上一条失败时先停下，不继续后面的复制或恢复。

### 1. 准备独立输出目录

~~~powershell
Set-Location -LiteralPath 'D:\itm4'
$dockerExe = 'C:\Users\king\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe'
& $dockerExe compose start --wait --wait-timeout 60

$runId = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
$exportDir = Join-Path 'D:\itm4\backups' "container-$runId"
New-Item -ItemType Directory -Path $exportDir -ErrorAction Stop | Out-Null
$containerBackup = "/app/backups/container-$runId.sqlite3"
$backupFile = Join-Path $exportDir 'snapshot.sqlite3'
$restoreFile = Join-Path $exportDir 'restored.sqlite3'
$exportDir
~~~

每次新的完整演练使用新目录，不覆盖之前的备份。若从中间重试，先核对变量及文件是否已经存在。

### 2. 在容器里生成快照，然后导出

~~~powershell
& $dockerExe compose exec -T app python -m app.backup backup --output $containerBackup
~~~

应显示 operation=backup、verified=true，保存五项统计。
先确认上面的 backup 成功，再执行：

~~~powershell
if (Test-Path -LiteralPath $backupFile) {
    throw '导出文件已经存在，先核对旧文件，不覆盖。'
} else {
    & $dockerExe compose cp "app:$containerBackup" $backupFile
}
& '.\.venv\Scripts\python.exe' -m app.backup verify $backupFile
~~~

compose exec 后的 python 是容器里的 Python，.venv\Scripts\python.exe 是 Windows 上的 Python。
app: 前缀表示 Compose 服务 app 中的路径。cp 操作的对象是已完成的备份快照，不是运行数据库。
Windows 的 verify 应显示 verified=true，五项统计与 backup 一致。

### 3. 核对导出文件内容一致

~~~powershell
$containerHash = & $dockerExe compose exec -T app python -c 'import hashlib,sys; from pathlib import Path; print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())' $containerBackup
$windowsHash = (Get-FileHash -LiteralPath $backupFile -Algorithm SHA256).Hash
$windowsHash -ieq $containerHash.Trim()
~~~

预期为 True，表示容器快照和 Windows 导出文件字节一致。
哈希核对证明复制内容一致，不表示已经恢复，也不证明文件来源可信。

### 4. 在 Windows 恢复到新文件

~~~powershell
& '.\.venv\Scripts\python.exe' -m app.backup restore $backupFile --output $restoreFile
& '.\.venv\Scripts\python.exe' -m app.backup verify $restoreFile
Get-ChildItem -LiteralPath $exportDir | Select-Object Name, Length
~~~

应看到 snapshot.sqlite3 和 restored.sqlite3；工具可能同时留下 .lock 文件，这是锁机制的正常行为。
restore 与随后独立 verify 都应显示 verified=true，五项统计与本次 backup 一致。
重复恢复到同一文件会被拒绝；不删除旧文件绕过保护，改用新的恢复文件名。
恢复文件内容在新文件中，本轮巡小站继续使用原 Docker 数据卷，页面不会自动切换到这个恢复副本。
学习者的统计核对不等于逐条人工核对全部内容；本轮独立自动验证另外比较了四张表的每一行。

保留 Windows 导出备份与恢复副本作为本轮结果；不要添加进 Git。
容器内快照可暂时保留，后续删容器不影响已完成的 Windows 导出文件。

理解问题：为什么只在容器的 /app/backups 里生成备份还不够？哪一步说明已经有了一个恢复副本？

## 自动验证与边界

2026-10-02：真实练习数据库只做 verify，仍为 2 个目标、51 条检查、3 次事件、2 个暂停目标、最大目标编号 2。
使用独立项目 xun-backup-export-verify-a9787c6759、新卷和明确标注的模拟数据测试：
- 模拟数据为 1 个目标、3 条检查、1 次已恢复事件、1 个暂停目标、最大目标编号 2（已删除目标编号保留）。
- 在容器内调用现有 backup 生成快照，经 compose cp 导出到 Windows 临时目录（目录名包含空格）。
- 容器与 Windows 快照 SHA-256 相同。
- 删除该独立测试容器与测试卷后，Windows 导出文件仍可校验、恢复到新文件并再次校验。
- 五项统计一致，并对 targets、target_id_sequence、checks、incidents 四张表逐行比较一致。
- 初次临时目录清理发现测试脚本的 SQLite 连接没有显式关闭；验证完成后补充显式关闭连接并重测恢复与逐行比较，临时文件清理成功。应用备份工具本身已使用 closing，不需要修改。
- 所有临时测试容器、卷和导出文件已清理。没有为学习者实际数据库生成备份，也没有修改它；真实导出操作留给学习者。
- 已通过 git check-ignore 核对计划中的导出与恢复路径被忽略。

2026-10-02 学习者报告执行完成，并提供终端截图；本轮亲手操作及理解验收通过：
- 导出目录为 backups/container-20261002-160354-629-8e8e38。
- 容器快照与 Windows snapshot.sqlite3 的 SHA-256 比对返回 True。
- restore 输出指向该目录中的 restored.sqlite3，verified=true；随后对该恢复文件独立 verify 也返回 verified=true。
- 五项统计为 targets=2、checks=51、incidents=3、paused_targets=2、last_target_id=2，与本轮准备时实际库统计一致。
- snapshot.sqlite3 与 restored.sqlite3 均为 40960 字节，另有 restored.sqlite3.lock；已只读核对这些文件存在。
- 已通过 git check-ignore 再次核对真实导出与恢复文件均被 Git 忽略，不包含在本轮代码文档提交中。
学习者正确说明：未挂载卷的容器 /app/backups 文件会随容器删除而丢失，需要导出保留。
本次确认恢复到独立新文件，没有替换正在使用的 Docker 数据卷；不将其表述为应用已切换到恢复库。
学习者已亲手提交，2026-10-02 核对提交为 c40d28b（docs: verify container backup export and restore）。当前计划剩最后一轮项目展示整理。
本轮没有自动切换运行库、演练整台机器丢失后的恢复，或建立异地备份。

## 官方参考

[Docker Compose cp：在服务容器与本机文件系统之间复制文件](https://docs.docker.com/reference/cli/docker/compose/cp/)
