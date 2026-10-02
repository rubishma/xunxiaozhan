# 第十一阶段：容器检查 Windows 测试网站

## 本轮范围

上一轮完成提交 df50e3d，本轮使用已经运行在 Docker Desktop 中的巡小站。
新增 examples/windows-site/index.html 作为明确标注的本机练习网站；不改变应用代码、数据库结构、Dockerfile 或 Compose。
不需要重新 build。练习网站运行在 Windows Python 中，不在 Docker 镜像中。

截至本轮开始，按目前展示项目的范围预计还剩四轮（含本轮）：
1. 容器网络：检查 Windows 上的可控网站，练习正常、故障、恢复。
2. 重建与数据保留：辨别 stop/start 和删除重建，确认命名数据卷的作用。
3. 容器数据库备份、导出与恢复演练：保留原数据，验证独立恢复副本。
4. 项目展示：README、架构图、真实演练记录及明确标注的静态演示材料。
如后续新增鉴权、通知或公网部署，则另算范围；本计划不包含这些功能。

## 地址要看请求从哪里发出

| 请求发起位置 | 地址 | 访问对象 |
| --- | --- | --- |
| Windows 浏览器 | http://127.0.0.1:18080/ | 经端口映射进入巡小站容器的 8000 |
| 巡小站容器 | http://127.0.0.1:8000/api/health | 巡小站容器自己的接口 |
| Windows 浏览器 | http://127.0.0.1:18765/ | Windows 上的测试网站 |
| 巡小站容器 | http://host.docker.internal:18765/ | Windows 宿主机上的测试网站 |

127.0.0.1 表示发起请求的环境自己。浏览器与容器中的同一个地址，不一定到达同一个服务。
Docker Desktop 提供 host.docker.internal 名称用于访问宿主机；不需要手工填写易变化的 IP。
这是容器主动访问宿主服务，不是给巡小站新增端口映射，因此不把 18765 写入 Compose ports。
这里的宿主机是运行 Docker Desktop 的这台 Windows 电脑。
本轮只验证这台 Windows + Docker Desktop 环境，不据此假设其他 Linux 部署环境配置相同。

## 亲手操作

先确认 Docker Desktop 运行，巡小站页面 http://127.0.0.1:18080/ 可以打开。
若之前主动停止了巡小站，在启动测试网站前执行：

~~~powershell
Set-Location -LiteralPath 'D:\itm4'
$dockerExe = 'C:\Users\king\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe'
& $dockerExe compose start
& $dockerExe compose ps
~~~

### 1. 在 Windows 启动测试网站

在 PowerShell（可使用 Codex 内置终端）执行：

~~~powershell
Set-Location -LiteralPath 'D:\itm4'
& '.\.venv\Scripts\python.exe' -m http.server 18765 --bind 127.0.0.1 --directory '.\examples\windows-site'
~~~

这个窗口持续运行，暂时不会返回输入提示符。保留窗口，去浏览器操作。
浏览器打开 http://127.0.0.1:18765/，应看到“Windows 测试网站”和标识 xun-windows-network-practice。
仅提供练习页面目录；不要把项目根目录或 data 目录作为网站根目录。

### 2. 添加独立目标并检查

打开巡小站 http://127.0.0.1:18080/，在“添加目标”中填写：
- 名称：Windows 网络练习站
- 网址：http://host.docker.internal:18765/

添加后记下实际分配的目标编号；后续操作都选择这个编号。
保留上一轮的“容器自身接口练习”目标，不用改写它。
立即检查应显示正常、HTTP 200；Windows 服务终端应出现本次 GET 请求记录。
巡小站只按最终 HTTP 状态判定，正常不能证明网页全部业务功能正常。

### 3. 停止网站，观察故障

回到运行 18765 测试服务的终端，按 Ctrl+C，只停止这个测试网站。
在巡小站中对同一目标立即检查，应显示异常、未收到 HTTP 响应。
故障事件应出现未恢复事件；巡小站 18080 本身仍可访问。
保留目标网址不变；不要用改网址替代停服务。

### 4. 恢复网站，观察恢复

在刚才的 Windows 终端重新执行同一条 http.server 命令。
回到巡小站检查同一目标，应重新得到 200。
查看历史与事件：保留正常、失败、恢复的记录，原未恢复事件变为已恢复。
后台默认每 60 秒检查，操作期间可能额外产生记录，不要求历史恰好只有三条。

练习结束先暂停这个练习目标，再按 Ctrl+C 停止 Windows 测试服务，避免继续积累计划内的连接失败。
暂停保留历史与事件，不删除测试目标。

理解问题：Windows 浏览器能访问 http://127.0.0.1:18765/，为什么巡小站目标要填 http://host.docker.internal:18765/？

## 验证记录与边界

2026-10-02 核对：巡小站容器 healthy，宿主映射 18080 → 容器 8000；本机 18765 当时未监听。
使用项目虚拟环境在 Windows 临时启动同目录页面，仅绑定 127.0.0.1:18765。
从现有巡小站容器另起 Python HTTPX 探测进程，保持 trust_env=False，实际结果为：
- Windows 回环地址：HTTP 200，页面标识匹配。
- 容器的 127.0.0.1:18765：ConnectError，连接被拒绝。
- 容器的 host.docker.internal:18765：HTTP 200，页面标识匹配。
- 停止 Windows 服务后再次访问宿主地址：ConnectError。
- 重新启动 Windows 服务后再次访问宿主地址：HTTP 200，页面标识匹配。

临时测试服务已停止；探测不调用巡小站检查接口，不新增目标或测试历史。
现有后台巡检继续按自身计划运行。本轮未修改防火墙、系统代理或 Docker 网络设置。
2026-10-02 学习者报告完成，并提供四张应用页面截图；目标 #2 的亲手检查、故障、恢复验收通过：
- 北京时间 11:36:54，手动检查 host.docker.internal:18765 返回 HTTP 200，耗时 16 ms。
- 11:37:29，手动检查连接失败，未收到 HTTP 响应，耗时 10.9 ms。
- 11:38:21，手动检查恢复 HTTP 200，耗时 215.4 ms。
- 事件 #2 显示“已恢复”，首次异常为 11:37:29、首次发现恢复为 11:38:21，失败检查次数为 1。
这些是页面显示的检查发现时间，不作为网站真实故障持续时间的精确测量。
学习者回答指出请求发起位置不同。已补充：两种访问方式到达同一个 Windows 网站；
127.0.0.1 随请求所处环境指向自己，容器中的该地址不会自动指向 Windows。
截图未单独展示练习结束后的暂停状态或测试服务退出，不将这两项记为已核实。
本轮验收完成；学习者已亲手提交，2026-10-02 核对提交为 b935573（docs: add container-to-Windows network exercise）。当前计划还剩三轮：重建与数据保留、容器备份恢复、项目展示。
本轮仅新增静态测试页与文档，没有再次运行未改变的应用全量测试。

## 排障

- 18765 启动提示地址占用：保留错误，先查看具体进程；不要停止不明服务。
- Windows 浏览器也连不上 18765：先确认 http.server 没有退出，地址及端口正确。
- 浏览器可访问但容器失败：核对目标使用 host.docker.internal，保留错误与 Docker 状态；不直接关闭防火墙。
- 页面尚能显示不等于服务在运行，停止后用新检查结果判断。
- 目标列表中名称重复时按编号选择；同网址已存在时使用已有目标。

[Docker Desktop 官方说明：容器访问宿主服务](https://docs.docker.com/desktop/features/networking/networking-how-tos/#connect-a-container-to-a-service-on-the-host)
