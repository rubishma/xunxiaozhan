# Docker 环境准备

## 已核对的结果

2026-10-02：Windows 11 家庭版已升级到 25H2，构建 26200.9457。
WSL 3.0.1.0 已安装，默认版本为 2，虚拟机平台已启用，HypervisorPresent 为 true。
WSL1 未启用的提示不影响此次 WSL2 搭建。
最初 wsl --install --no-distribution 返回 HTTP 403，安装文件获取未成功；
随后学习者通过微软官方 x64 MSI 完成安装和虚拟机平台启用，并提供重启后的输出。

学习者已安装 Docker Desktop 4.93.0，并提供 Engine running 的截图。
进一步命令行核对：Docker Engine 29.8.1、Compose v5.5.1、desktop-linux 上下文，服务器为 linux/amd64。
检查时没有运行中的容器，WSL 中只有 docker-desktop 发行版。
本机 Docker 命令的完整路径为：

~~~text
C:\Users\king\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe
~~~

## 内存限制与第一个容器

此前 Docker 报告内存上限 8175308800 字节，约 7.6 GiB，实际用量与上限不同。
检查确认用户没有 .wslconfig，现已创建 C:\Users\king\.wslconfig：

~~~ini
[wsl2]
memory=4GB
~~~

此设置作用于当前用户的 WSL2 虚拟机整体，非单个容器；不代表始终占用 4 GB。
生效需要 WSL 虚拟机彻底停止后重新启动。只修改文件不表示运行中的引擎已采用新上限。
CPU 与其他配置保持默认；后续可依据实际工作负载调整。

学习者在 PowerShell 按顺序执行：

~~~powershell
$dockerExe = 'C:\Users\king\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe'
& $dockerExe desktop stop --timeout 60
wsl --shutdown
& $dockerExe desktop start --timeout 90
& $dockerExe info --format '内存上限（字节）={{.MemTotal}}'
& $dockerExe run --rm hello-world
~~~

逐条执行，上一条失败时先反馈，不继续执行后续步骤。
检查时没有其他 WSL 发行版；以后若增加其他发行版，wsl --shutdown 会同时停止它们。
info 应显示接近 4 GB 的值，可能小于 4294967296，因为系统会保留部分内存。
hello-world 首次运行会从 Docker Hub 下载官方测试镜像，应显示 Hello from Docker!。
--rm 会删除退出后的测试容器，镜像仍保留；因此 Containers 页面没有运行中的容器是正常的。
若镜像拉取报错，保留原始错误，先核对网络与日志，不擅自更换第三方镜像源。

## 验收边界

2026-10-02 学习者提供实际输出：MemTotal=4106817536 字节（约 3.82 GiB），4 GB 上限已生效；官方 hello-world 镜像拉取成功并输出 Hello from Docker!。安装、引擎、Compose、内存限制及测试容器运行验收通过。
本阶段结束时巡小站尚未容器化；后续实现与验收另见第十阶段。
此阶段没有挂载运行数据库；数据库备份和恢复仍沿用上一阶段工具。
理解验收：学习者最初认为没有真正启动容器，经解释后确认容器已被删除。实际过程为创建并启动容器、程序输出后退出、--rm 删除退出的容器；镜像保留。

## 官方说明

- https://docs.docker.com/desktop/setup/install/windows-install/
- https://learn.microsoft.com/en-us/windows/wsl/wsl-config
- https://docs.docker.com/reference/cli/docker/desktop/
