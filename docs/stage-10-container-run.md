# 第十阶段：在容器中运行巡小站

## 本轮目标与文件

让巡小站作为持续运行的服务启动，在 Windows 浏览器通过 18080 访问。
本轮不迁移旧数据库、不发布公网，也不增加通知等业务功能。

- Dockerfile：用官方 Python 3.13.15 slim-bookworm 镜像安装固定依赖、复制 app，并启动一个 Uvicorn 进程。
- .dockerignore：构建时仅允许应用源码、requirements.txt 和 Dockerfile；不会发送 .venv、Git 历史、data、backups 或无关 HTML。
- compose.yaml：记录端口、60 秒检查间隔、独立数据卷、健康检查及停止等待时间。
- 程序以 UID 10001 的普通用户运行；只给数据与备份目录写权限。

Dockerfile 中的 CMD 使用 exec 形式，使服务接收停止信号；Compose 最多等待 20 秒，覆盖应用单次检查的 10 秒总时限。
保持单 worker，不使用热重载，不开启自动重启；本轮由学习者明确启动、停止服务。

## 三个概念

镜像是构建出的程序与依赖；容器是用该镜像启动的运行实例；数据卷单独保存 SQLite。
修改本机源码后，需要重新 build 并更新容器，运行中的容器不会自动读取本机 app。

端口映射为：Windows 的 127.0.0.1:18080 → 容器的 8000。
容器内部服务监听 0.0.0.0，才能接收转发到容器网卡的请求；宿主端口仅绑定 127.0.0.1。
Dockerfile 的 EXPOSE 是端口说明，实际发布由 Compose 的 ports 完成。

Compose 默认项目名 xunxiaozhan，卷名 xunxiaozhan_xun-data，挂载到 /app/data。
这不是 Windows 的 D:\itm4\data；第一次启动产生全新数据库及默认目标。
旧目标、历史和备份仍在 Windows 原目录中，因此两个页面的数据不同是预期结果。
数据卷保留不等于已有备份；Docker 中的备份导出另行演练。

## PowerShell 操作

先保持 Docker Desktop 的 Engine running。以下只给当前 PowerShell 补充 Docker 目录，关闭窗口后失效，不修改系统 PATH。
完整路径能找到 docker.exe，但构建时的凭据助手仍需要通过 PATH 找到。

~~~powershell
Set-Location -LiteralPath 'D:\itm4'
$dockerBin = 'C:\Users\king\AppData\Local\Programs\DockerDesktop\resources\bin'
$dockerExe = Join-Path $dockerBin 'docker.exe'
$env:PATH = "$dockerBin;$env:PATH"
& $dockerExe compose config --quiet
& $dockerExe compose up -d --build
& $dockerExe compose ps
& $dockerExe compose logs --tail 40 app
~~~

逐条执行，上一条失败就停下并保留错误。首次构建会下载官方基础镜像和 Python 依赖。
-d 表示后台运行，PowerShell 返回提示符后容器继续工作；不是把程序变成一次性任务。
ps 应显示 Up，健康检查稍后变为 healthy。healthy 仅表示 /api/health 可响应，不证明目标网站或数据库健康。

打开 http://127.0.0.1:18080/；Windows 上原先的 8000 服务若还开着，两个服务可以并存。

## 本轮亲手验收

1. 执行启动命令，在 Docker Desktop 中展开 xunxiaozhan，确认 app 持续运行；打开 18080 页面。
2. 全新库默认目标的 127.0.0.1:8765 指向容器自己。容器里没有该测试服务，检查失败属于预期，不能据此认定容器启动失败。
3. 将该练习目标名称改为“容器自身接口练习”，网址改为 http://127.0.0.1:8000/api/health，保存并立即检查，应为 HTTP 200。这只练习调用容器自身 API，不代表验证了其他网站。
4. 执行下面的 stop；刷新 18080 页面应无法连接，ps -a 应显示 Exited。已有页面可能仍显示旧结果，不表示服务还在运行。
5. 执行 start 后再次打开 18080，目标名称与检查记录应仍在。这只验证同一容器停止后再启动；删除并重建容器的数据保留下一轮再练。

~~~powershell
& $dockerExe compose stop
& $dockerExe compose ps -a
& $dockerExe compose start
& $dockerExe compose ps
~~~

本轮无需执行 down。尤其不要执行 down -v：它会连同本项目数据卷一起删除。
关闭浏览器不会停止巡检；练习结束可 compose stop，下次 compose start。
容器访问 Windows 测试网站的 host.docker.internal 与容器间服务名，留到下一轮网络练习。

理解问题：浏览器访问的是 18080，为什么容器内的 Uvicorn 仍然使用 8000？

## 排障与验证记录

- 若 18080 被其他程序占用：保留错误并先查占用，不停止不明进程。
- 凭据助手 executable file not found in PATH：先执行上面的当前终端 PATH 设置。
- 镜像拉取 EOF：这次曾发生在 auth.docker.io；随后单独拉取官方 Python 镜像成功，未改变镜像源，也未据此认定长期网络根因。
- 修改 requirements.txt 后构建失败：保留 pip 完整错误，不随意取消版本约束。
- 查看启动失败原因用 compose logs --tail 80 app；不要删数据库来消除报错。

2026-10-02 自动验证通过：
- 官方 Python 3.13.15-slim-bookworm 镜像拉取及应用镜像构建成功，固定的全部 Python 依赖可在 Linux/amd64 安装。
- 基础镜像本次解析的摘要：sha256:2325bb286ec344af3e5898cc224b5844e2707ac6e26b1632516fd3edc84a5e26。Dockerfile 固定版本标签，未按摘要锁死；标签未来仍可能重新发布。
- 在 --network none 的一次性容器中只读挂载 tests，49 项 Python 自动测试全部通过（10.627 秒），包括 Linux 进程锁与临时数据库备份恢复。
- 仍有既有 Starlette 测试客户端弃用提示，不影响本次通过；没有为消除提示擅自换依赖。
- 检查运行用户 UID=10001、镜像内 data 为空，未包含 .venv 或 .git。
- 使用临时 Compose 项目 xun-stage10-verify-f5c3a7b364 和它独立的新卷，实际验证宿主 8080 页面、图表脚本、接口及手动 HTTP 200 检查。
- stop/start 后名称、暂停状态及检查历史一致；日志显示调度器正常停止、启动，容器恢复 healthy。
- 已移除该临时测试项目的容器、网络及测试卷，保留 xunxiaozhan:local 镜像供学习者操作。
- 未挂载 Windows 原运行数据库，也未创建学习者正式练习用的 xunxiaozhan 数据卷。

2026-10-02 学习者报告完成，并提供三张截图，亲手操作验收通过：
- 容器内目标 http://127.0.0.1:8000/api/health 的手动检查为 HTTP 200，耗时 9 ms，检查时间为北京时间 11:05:48。
- 停止后浏览器访问 127.0.0.1:18080 显示 ERR_CONNECTION_REFUSED。
- 再启动后的页面仍显示相同的检查时间、耗时和结果，确认该条已保存记录保留。
- 学习者回答“因为巡小站是在 Docker 中运行的”。已补充解释：关键是 compose up -d 的后台运行方式，以及容器中 Uvicorn 进程持续运行；在 Docker 中运行本身不保证程序长期存活，hello-world 就会执行完退出。
本轮操作验收完成；学习者已亲手提交，2026-10-02 核对提交为 df50e3d（feat: run app locally with Docker Compose）。
本轮学习者未演练删除并重建容器后的数据保留、访问 Windows 目标网站或备份导出，后续分轮练习；端口调整中进行过一次配置驱动的容器重建，见下方记录。

## 官方参考

- [Dockerfile 指令](https://docs.docker.com/reference/dockerfile/)
- [Compose 网络与端口](https://docs.docker.com/compose/how-tos/networking/)
- [Docker 数据卷](https://docs.docker.com/engine/storage/volumes/)

2026-10-02 端口调整：学习者说明本机 8080 留给另一项目使用，当前配置及操作步骤改为 18080；上面的 8080 自动验证记录保留为当时结果。
已应用配置并重新创建 xunxiaozhan-app-1，确认前后挂载同一个 xunxiaozhan_xun-data 卷。18080 页面与 /api/health 均返回 HTTP 200，容器为 healthy；无需重新构建镜像。
