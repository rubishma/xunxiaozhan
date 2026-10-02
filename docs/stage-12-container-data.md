# 第十二阶段：重建容器，保留数据

## 本轮目标

上一轮网络练习已提交为 b935573。
本轮亲手删除并重新创建巡小站容器，观察容器 ID 改变，而命名数据卷及其中的数据库继续保留。
完成后还剩两轮：容器数据库备份恢复、项目展示。
不增加应用功能，不修改 Compose 或镜像；只验证当前配置已有的数据持久化行为。

## 容器与数据分别放在哪里

当前 Compose 项目为 xunxiaozhan，服务为 app：
- 容器名称通常是 xunxiaozhan-app-1，重建后这个名称可以相同。
- 容器 ID 是运行实例的身份，重建后应不同；不要只看名称判断是否重建。
- 命名数据卷 xunxiaozhan_xun-data 挂载到 /app/data。
- 数据库 /app/data/xunxiaozhan.sqlite3 及锁文件位于该卷内。
- /app/backups 当前没有挂载卷，在其中另建的文件属于容器可写层，删容器会丢失；下一轮练习将备份导出到 Windows。
- Windows 的 D:\itm4\data 是早期本机运行使用的目录，不是这个 Docker 命名卷。

| 操作 | 容器 | 命名数据卷 |
| --- | --- | --- |
| compose stop / start | 停止、启动原容器，ID 不变 | 保留 |
| compose down / up | 删除、创建新容器，ID 改变 | 普通 down 保留，up 重新挂载 |
| 带 --volumes 或 -v 的 down | 删除容器 | 会删除配置中声明的命名卷，不用于本轮 |

保留同一 Compose 项目名和同一卷配置。若改用另一个项目名或挂载新卷，看到空库不等于旧卷已删除。
数据卷不是备份；它不能防止误删卷、Docker 数据重置或磁盘损坏。

## 学习者操作

逐段执行，任何命令失败先停下并保留错误，不继续下一步。
全程使用同一个 PowerShell 窗口和 D:\itm4 目录，不增加 -p 参数，也不使用任何 prune 命令。

### 1. 启动原容器并暂停所有目标

本轮准备时 Docker 引擎已由助手启动；原巡小站容器处于 Exited (0)，尚未重建。
在 PowerShell 执行：

~~~powershell
Set-Location -LiteralPath 'D:\itm4'
$dockerExe = 'C:\Users\king\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe'
& $dockerExe compose start --wait --wait-timeout 60
& $dockerExe compose ps
~~~

打开 http://127.0.0.1:18080/，依次暂停所有目标，已暂停的保持不动。
暂停让记录统计在练习期间保持稳定，避免后台新检查影响前后比较。
无需启动 Windows 的 18765 测试网站；本轮不发起网站检查，也不删除任何目标。
查看目标 #2 的历史和已恢复事件；若实际编号不同，按自己的目标编号。

### 2. 记录重建前状态

~~~powershell
$beforeId = & $dockerExe compose ps -q app
$beforeId
& $dockerExe inspect $beforeId --format '{{range .Mounts}}{{println .Name .Destination}}{{end}}'
& $dockerExe compose exec -T app python -m app.backup verify /app/data/xunxiaozhan.sqlite3
~~~

应看到 xunxiaozhan_xun-data 挂载到 /app/data；verify 应显示 verified: true。
保存五项统计：targets、checks、incidents、paused_targets、last_target_id。
这里的 verify 是只读校验，不创建备份；不能把成功校验当成完成备份。

### 3. 删除容器，确认数据卷还在

~~~powershell
& $dockerExe compose down
& $dockerExe compose ps -a
& $dockerExe volume inspect xunxiaozhan_xun-data --format '{{.Name}}'
~~~

重要：只执行普通 down，不添加 -v 或 --volumes。
此时 compose ps -a 不应列出原 app 容器，volume inspect 仍应返回卷名；浏览器 18080 暂时不能访问。
Docker 命名卷保留在 Docker 管理的存储中，并不依赖一个仍在运行的容器。
如果找不到数据卷，先停止后续操作并排查，不通过创建空卷掩盖问题。

### 4. 用原镜像创建新容器

~~~powershell
& $dockerExe compose up -d --no-build --wait --wait-timeout 60
$afterId = & $dockerExe compose ps -q app
$afterId
$beforeId -ne $afterId
& $dockerExe inspect $afterId --format '{{range .Mounts}}{{println .Name .Destination}}{{end}}'
& $dockerExe compose exec -T app python -m app.backup verify /app/data/xunxiaozhan.sqlite3
~~~

预期：ID 比较结果为 True，仍挂载 xunxiaozhan_xun-data，五项统计与之前一致。
--no-build 表示使用已有镜像，这次是新建容器，不是重新构建镜像；镜像若不存在会报错。
使用 up 是因为原容器已删除，单独 start 无法创建它。

重新打开页面，确认目标编号、名称、暂停状态、旧检查时间和故障事件都保留。
前后统计一致只能核对数量与编号上限，页面核对则检查所选旧记录；不能宣称仅靠统计已逐行验证整个数据库。
本轮结束可以保持目标暂停，或执行 compose stop 停止服务；下一轮备份会另行启动。

理解问题：容器 ID 已改变，为什么原来的检查历史还在？如果数据库只写在没有挂载卷的容器目录里，会怎样？

## 自动验证与验收边界

2026-10-02 只读核对：原容器 ID 为 cb7233ba105abe44dca0f656d8f389d3dbd0853667b113ba7a7ee4d069dc8d8f，挂载 xunxiaozhan_xun-data；其状态为 Exited (0)。
初次核对时 Docker 引擎未运行，随后已启动 Docker Desktop，原容器与卷均可读取。
独立测试使用随机项目名、新卷和明确标注的模拟数据，不挂载用户原卷，也不重建用户实例。
自动验证通过：临时项目 xun-recreate-verify-5f6c42d146 使用自己的独立新卷，未发布宿主端口。
- 准备明确标注的模拟目标、三条正常/失败/恢复记录、一条已恢复事件、暂停状态及已删除目标的编号序列。
- 普通 down 后旧容器消失，命名测试卷仍能 inspect。
- up 后容器 ID 从 6a2d119043f5b8dbaac1015784941fcb7232bc7f4b14490647e3ca696ef6b632 变为 b00e7a423141b31e2e2d3233b472077ce719dd5dd89e193c52653e7c22433fbc，仍挂载同一个测试卷。
- targets、target_id_sequence、checks、incidents 全表逐行比较一致（该版本共四张表）；新建目标获得编号 3，未复用已删除编号 2。
- 未挂载卷的 /app/backups 下专用临时标识文件随旧容器消失；没有涉及用户的实际备份文件。
- 测试完成后仅清理该随机项目的容器、网络和模拟数据卷。

2026-10-02 学习者报告已完成全部操作，并说明容器 ID 改变而数据仍保留；本轮亲手操作与理解验收完成。
提供的重建后截图确认：
- xunxiaozhan_xun-data 仍挂载到 /app/data。
- 对 /app/data/xunxiaozhan.sqlite3 执行 verify，返回 verified=true。
- targets=2、checks=51、incidents=3、paused_targets=2、last_target_id=2。
截图展示的是重建后的挂载与校验结果；容器 ID 改变及全部步骤完成依据学习者报告，未把单张截图表述为完整前后对照证据。
学习者正确解释数据卷保留、未挂载卷的容器目录随容器删除而丢失。补充明确：停止容器不会删除其可写层，删除容器才会。
本轮未创建或导出数据库备份；verify 成功是完整性与结构校验通过，不等于已备份。
Git 提交留给学习者亲手执行，待确认。还剩两轮：容器数据库备份恢复、项目展示。

## 官方参考

- [Compose down 默认删除范围及卷选项](https://docs.docker.com/reference/cli/docker/compose/down/)
- [数据卷的生命周期](https://docs.docker.com/engine/storage/volumes/#a-volumes-lifecycle)
