# 交接文档 — 《荒野行动Plus》本地启动 / 本地服务端探索

最后更新：2026-11-22 11:08 (Asia/Shanghai)
工作目录：`I:\hyxd\hyp`（当前 session cwd 即此目录）
本次任务目标：**本地启动游戏并单机游玩，最好本地搭建服务端**

---

## 0. 一句话状态

- ✅ **客户端可以本地启动到登录界面**（已多次实测并截图）。
- ✅ **更新服 / 服务器列表服 / 登录端点的传输层**均已本地伪造成功。
- ❌ **尚未进入对局**：已新增与 SDK 解析字段对应的授权轮询服务，完整客户端联调、大厅及战斗协议仍待实现。96 字节请求的用途尚未确认。
- **当前为本机联调配置，尚未还原**：SDK URL、update.ini、hosts 标记块、代理排除列表见 §10–§12；ServerList 已指向本机 18443。系统代理仍开启。原文件保留在 backups/sdk-integration-20261122-101249。

---

## 1. 环境事实（不要重复验证，除非怀疑变化）

### 1.1 客户端
| 项 | 值 |
|---|---|
| 游戏 | 《荒野行动Plus》(Knives Out Plus / 网易 PGX) |
| 版本 | 3.0.43（网吧版压缩包 `《荒野行动Plus》网吧版.zip`，内含 `pgx-3.0.43.7z.001~003`） |
| 官方停服 | **2021-11-22 15:00**（Cloudflare/Google DoH 查 `game.hyp.163.com`、`pgx.gph.netease.com` 均无 A 记录） |
| 安装路径 | `I:\hyxd\hyp`，注册表卸载项指向 `I:\hyxd\新建文件夹\hyp\Uninstall.exe` |
| 主程序 | `game\hyp_beta.exe`（21MB，VMProtect 保护，明文字符串扫描全部为 0） |
| 反作弊 | `game\hypprotect.exe` + `vmprotectsdk64.dll` |
| 资源 | `res\*.pg`，魔数 `79 74 67 70` ("ytgp")，其后是密文，无明文 TOC；`hukou.pg` 地图 1.2GB |

### 1.2 启动链（实测）
```
hyp_start.exe                    requireAdministrator
  └ bootloader.exe               CEF 启动器 UI，本地 IPC :11199
     └ hypprotect.exe <handle>   反作弊壳
        └ game\hyp_beta.exe -p 1 -redirect <handle>
           ├ BrowserProxy.exe ×3（CEF 渲染/GPU 子进程，无网络）
           └ 内嵌 CEF 承载 OAuth 登录页
```

### 1.3 卡点原样复现
| 现象 | 根因 | 处理 |
|---|---|---|
| 启动器右侧「无法安全地连接到此页面」 | 2018 版 CEF 只支持 TLS 1.0/1.1，官网已升级 | 装饰性，忽略 |
| 「正在更新 0 KB/s」 | `update.ini Version=3.0.43`，服务器最新 3.0.103，去拉 `patch3.0.44.pg` → CDN 404 | 改 `Version=3.0.999` 跳过 |
| 游戏内登录界面白板 | CEF 加载 `https://hyp.swjoy.com/oauth2/authorize.do`，该域公网无 A 记录 | 见 §3 |

### 1.4 网络拓扑（实测）
```
客户端进程 → 127.0.0.1:443    (SNI=game.hyp.163.com)   POST /s/chatft.s
客户端进程 → 106.2.95.81:8989                          TCP SynSent（无响应）
客户端进程 → serverlist.ini 里的 59.111.11.x:25000      大厅服，已下线
CEF 子进程  → https://hyp.swjoy.com/oauth2/authorize.do?client_id=3525
              &redirect_uri=https://mgbcb.matrix.netease.com/pgx/cb/authcallback/swjoydy/pc/defaults/<32hex>
              &response_type=code&state=
```

⚠️ 用户路由器/Clash 把 `hyp.swjoy.com`、`pgx.gph.netease.com`、`cdn.hyp.163.com` 解析到 `127.0.0.1`；这是用户环境的既有配置，**不是我们改的**。

---

## 2. 已完成的成果

### 2.1 能玩到的位置
双击 `_local_server\start_game.bat`，点「进入游戏」→ EULA「同意」→ **客户端稳定停在登录界面（白板 + LOGO）**。到此为止，无服务端可继续。

### 2.2 传输层全部可伪造（已验证）
| 层 | 工具 | 验证结果 |
|---|---|---|
| 更新服 / 服务器列表服 | `localsrv.py` (:80) | 启动器确实来拉 `/pl/patch_list3.txt` |
| 登录端点 | `tls_login.py` (:443) | **TLS 1.0 握手成功**，客户端发 `POST /s/chatft.s` |
| OAuth 授权页 | `oauth_fake.py` (:443) | 自签 SAN 证书可被 TLS 客户端接受；CEF 也曾在 01:16:22 命中 `/oauth2/authorize.do` |

### 2.3 抓到的关键载荷
`logs\req_20261122_010417_001.bin`（231 字节，含 HTTP 头 + 96 字节密文）：

```
POST /s/chatft.s HTTP/1.1
Host: game.hyp.163.com
Content-Length: 96
Content-Type: application/x-www-form-urlencoded

c6fa6926eef755a409c4ad8f5be1ab716324213847e9b8d80e687133a6f088351d
8e772d32161514c1f831b1a3acf908bd34025d9d8841d6f520c0d33dbf3d2363e9
6b90f445c5f98bb9c40ec7409c3f85eee7cccffb57f6cb517830f9e76d0b
```
- 96B = 6×16 = 12×8，两种分组都对齐
- 熵 6.348 bit/byte，可打印字符 36%，无 ECB 重复块、无 XOR 特征
- **两次抓包完全一致** → 固定载荷，不含随机 nonce（对逆向有利）
- 尾字节 `0x0b` 疑似 PKCS7，但整块校验不成立

---

## 3. 未完成 / 阻塞点

| # | 阻塞 | 证据 | 可能突破口 |
|---|---|---|---|
| 1 | OAuth 身份源下线 | `hyp.swjoy.com` 公网无 A 记录；`swjoy.com` 主域存活 (203.107.63.144) 但带 `Host: hyp.swjoy.com` 返回 502 | 本地假授权页已写（`oauth_fake.py`），但 CEF 因**用户 Clash 代理 + 老 TLS** 未稳定连上 |
| 2 | 96B 密文不可解 | VMProtect，`hyp_beta.exe` 明文扫描为 0，`hypprotect.exe` 反调试 | 动态 dump 解密后内存 |
| 3 | 大厅/战斗协议缺失 | `serverlist.ini` 指向的 IP 全下线；无任何公开模拟器（GitHub 搜索 0 结果） | 需协议逆向 + 编写同步服务器，工作量以人月计 |

**当前结论**：目标「单机游玩」尚未达成。后续发现的 SDK 授权轮询接口见 §9，可继续自行实现兼容服务。现有抓包不足以判定 96 字节载荷是否阻断登录。

---

## 4. 关键踩坑（避免重蹈覆辙）

1. **权限屏障**：`hyp_start.exe`、`hyp_beta.exe` 均为 High integrity。cua-driver 运行在 Medium，`click` 会报 UIPI 错误。必须用 `Start-Process powershell.exe -Verb RunAs` 启动提权 helper（`probe_*.ps1`）做真实 `SendInput` 点击。用户 UAC 配置为 `ConsentPromptBehaviorAdmin=0`（静默提权），所以 RunAs 不会弹窗。
2. **`update.ini` 会被启动器改回**：实测提权脚本写入 `3.0.999` 后，启动器在读更新流程时会把它重写回 `3.0.43`。每次实验前都要重新写。
3. **CEF 代理问题**：用户装有 Clash Verge (`D:\Clash Verge\verge-mihomo.exe`)，系统代理 `127.0.0.1:7897`。CEF 老版本会把 `hyp.swjoy.com` 请求交给代理，导致连不上本地假服务。**测试本地 OAuth 前必须关 Clash 并把 `ProxyEnable` 设为 0**；实验后恢复为 1（用户原状）。
4. **TLS 版本**：客户端自身 HTTP 栈走 TLS 1.0；CEF 支持到 TLS 1.2 但**不支持 TLS 1.3**。`oauth_fake.py` 已设 `maximum_version=TLSv1_2`；`tls_login.py` 固定 `TLSv1`。
5. **openssl 不在 PATH**：用 `C:\Program Files\Git\usr\bin\openssl.exe`。
6. **不要占用 80 端口跑原始 tap**：`tls_tap.py` 占住 80 后启动器会无限重试 `patch3.0.44.pg`；测 OAuth 前停掉它。
7. **后台 job 会超时转后台**：长时间 `Start-Sleep` 的 pwsh 命令会转 job，用 `job_output(job_id, wait=true)` 收结果。

---

## 5. 工具清单

| 文件 | 用途 | 备注 |
|---|---|---|
| `start_game.bat` | ★ 一键启动到登录界面（跳过更新 + 清残留进程） | 用户主要交付物 |
| `restore_update.bat` | 还原 `update.ini` 版本号 | |
| `localsrv.py` | 本地更新服/列表服 (:80) + TCP 25000/25100 抓包 | 需配 `apply_hosts.ps1` |
| `tls_login.py` | ★ 登录端点探针：TLS 1.0 + 记录 `/s/chatft.s` | 已抓到 96B 密文 |
| `tls_tap.py` | 轻量 SNI 探针 | 会占 80 端口，注意 |
| `oauth_fake.py` | ★ 本地假 OAuth 授权页 + 回调（SNI 路由，SAN 证书） | 需关 Clash 才稳定 |
| `apply_hosts.ps1` / `restore_hosts.ps1` | hosts 接管/还原（管理员） | |
| `run_server.bat` | 启动 `localsrv.py` | |
| `probe_oauth.ps1` | 提权点击启动器 + EULA | 内部测试用，可删 |
| `server.pem/.key`, `oauth.pem/.key`, `*.cnf` | 自签证书 | 已生成，勿重复生成 |
| `logs\*` | 抓包证据 | 含 `req_*.bin`、`tls443.log`、`oauth.log` |

---

## 6. 当前环境状态（已清理）

| 项 | 状态 |
|---|---|
| `update.ini` | `Version=3.0.43`（原状） |
| `hosts` | 0 条非注释（原状） |
| `serverlist.ini` | 官方列表（原状） |
| 系统代理 | `ProxyEnable=1`（**用户原状，勿改**） |
| Clash | 运行中（**用户原状，勿杀**） |
| 游戏进程 | 0 |
| 80/443/25000/25100 | 全部释放 |

---

## 7. 下一步建议（按性价比排序）

1. **（若继续追单机）**：动态调试 `hyp_beta.exe`，在 VMProtect 解壳后 dump 内存，找 96B 密文的密钥/算法。需要先绕过 `hypprotect.exe` 反调试。成功率低，工作量大。
2. **（若只想玩）**：放弃此客户端，用 UE + FPS 模板自建；`res\*.pg` 需先解密才能复用美术资产。
3. **（若只是修启动）**：`start_game.bat` 已满足，可直接交付用户。

**给用户的诚实回答**：游戏能启动到登录界面，但官方服务端 2021 年就关了，登录身份源和游戏协议都拿不到，做不出真正的单机对局。这一点已经有完整的抓包证据支撑，不要含糊承诺。

## 8. 接手复核（2026-11-22）

- `logs/oauth.log` 记录了授权页请求和后续的客户端 HTTP 请求。现有日志缺少授权完成、进入大厅和创建对局的记录。`/s/chatft.s` 请求在登录阶段出现，但现有记录无法判定它是否控制登录或进图。
- `update.ini` 目前是 `3.0.43`。客户端自己的 `game/conf.xml` 仍记录此前测试使用的 `3.0.999`，两处版本值来自不同文件。复核时未看到游戏进程或此前的本地监听端口。
- 开发者复盘提到过离线调试场景碰撞，也明确描述了联机原型使用独立服务端和网络同步。当前网吧版的安装清单、配置和可执行文件中未找到与该离线调试对应的启动入口。[开发者复盘](https://www.gameres.com/854672.html)
- 官网存档列出的完整客户端分卷与现有 `pgx-3.0.43` 文件名一致。另有 `1.0.88`，官网注明它用于登录新功能体验服。较早的 `3.0.19` 不删档测试客户端也需要激活码并依赖测试服务器。尚未找到公开发行的离线构建。[官网下载页存档](https://web.archive.org/web/20220328113434id_/http://hyp.163.com/download/) · [不删档测试公告存档](https://web.archive.org/web/20180905175557/https://hyp.163.com/news/news/20180822/28592_771467.html)
- 单机启动的验收仍需看到原版客户端进入本地地图，能控制角色，且对局无需公网服务。目前最远验证到登录页。能推进验收的材料是匹配版本的离线测试构建，或附有本地启动说明的兼容服务端。

## 9. 自行实现 SDK 授权服务（2026-11-22）

已新增 `sdk_auth.py`，仅绑定 `127.0.0.1`。这是实际实现的 SDK 授权轮询服务，游戏账户验证及进图服务仍待实现。

### 原客户端提供的字段证据

分析对象为 `game/ntunisdkswjoy.dll` 的原始 x64 指令，地址以下使用 RVA，加载基址为 `0x180000000`。

- `0x89130` 的函数从 `UNISDK_JF_GAS3_URL` 拼接 `/query_auth_info`，JSON 请求含 `auth_index` 和 `login_channel`。原配置的基础地址为 `https://mgbsdktest.matrix.netease.com/pgx/sdk/`，渠道为 `swjoydy`。
- `0x89516` 读取 JSON 顶层 `code` 数值，`0x89519` 比较 `200`。旧 `oauth_fake.py` 返回的 `code: 0` 会进入继续轮询分支。
- `0x89554` 查找 `data` 对象，`0x895b4` 查找其中的字符串 `code`，并在 `0x8962a` 到 `0x89655` 将其写入 `SESSION`。
- `0x89584` 查找 `data.state` 字符串，`0x89659` 到 `0x89684` 将其写入 `CHANNEL_STATE`。`UID` 在该函数里写为字符串 `0`。
- `0x896a9` 比较顶层 `code` 是否为 `445`，符合时继续轮询。接口的 HTTP 状态与 JSON 内的 `code` 分开处理。

### 新实现与验证

- `GET /oauth2/authorize.do` 验证该版本的 client_id 和回调路径，创建本机短期授权，返回含状态值的 302。
- `POST /pgx/sdk/query_auth_info` 在授权前返回 `{"code":445}`，授权后返回 `{"code":200,"data":{"code":"本地会话值","state":"请求状态值"}}`。并发重试保留同一会话，过期后返回等待状态。
- `test_sdk_auth.py` 的 5 项检查通过，覆盖解析字段、状态转义、重复及并发请求、过期、错误输入、容量耗尽和响应字节长度。
- 使用原 `ntunisdkswjoy.dll` 导出的 `cJSON_Parse` 和 `cJSON_GetObjectItem` 验证上述响应，原生节点偏移与解析函数使用的偏移一致。
- 本机 HTTPS 实测通过 TLS 1.0 和 TLS 1.2 的轮询请求。测试监听随机本机端口，结束后关闭。游戏配置及代理未由本轮修改。
- 上述结果证明接口实现与已识别的 SDK 字段匹配。尚未实测原版游戏通过 SDK 登录，也未获得大厅的首次有效报文。

运行接口检查可在本目录使用 `python -B -m unittest -v test_sdk_auth`。运行 TLS 服务使用 `python sdk_auth.py --port 443`，沿用本目录现有证书。HTTP 检查可使用 `--plain-http --port 18443`。

继续联调时，需要使该客户端的 SDK 基础地址访问本机这个接口，并确认 `AUTH_INDEX` 对应的授权和轮询都到达新服务，再记录 SDK 完成后真实发送的游戏账户或大厅请求。此前交接提出的“先破解 96 字节才能继续”尚无因果证据。

## 10. SDK 原生消费验证及当前联调状态（2026-11-22）

- `sdk_auth.py --local-login` 可在首次有效的本机 SDK 轮询时发放测试会话。该模式供本地联调用，成功状态、会话字段及后续游戏账户验证仍分别验收。
- 新增 `native_sdk_probe.py`，校验原 SDK 的 SHA-256 后，在独立 Python 进程内加载原 DLL。使用原生 HTTP 函数 RVA `0x7e4c0` 请求随机本机端口的真实服务端，再把原生 HTTP 缓冲交给响应处理函数 RVA `0x89470`。
- 实测原 SDK 的 HTTP 请求及响应处理函数执行成功，随后从 SDK 虚表方法读取 `SESSION`、`CHANNEL_STATE`、`UID`，与本地应答逐一一致。该验证超出仅使用 JSON 解析器的检查范围。运行命令为 `python -B -u native_sdk_probe.py`。
- 接口检查增至 6 项，新增首次本机轮询发放稳定测试会话的用例，全部通过。
- 本轮启动器已经打开，显示版本 `3.0.999` 和“进入游戏”。自动点击和回车后尚未出现游戏进程。需要在启动器手动点击“进入游戏”，再继续观察原版进程中的 SDK 轮询和游戏请求。上述 SDK 单独进程验证不等同于原版游戏登录成功。
- **当前联调配置已与 §6 的历史还原状态不同**。`game/swjoydy.data` 的 `UNISDK_JF_GAS3_URL` 为 `http://127.0.0.1:18443/pgx/sdk/`，`update.ini` 为 `3.0.999`。原始两份文件备份在 `_local_server/backups/sdk-integration-20261122-101249/`。
- 本机服务 `python -B _local_server/sdk_auth.py --plain-http --port 18443 --local-login` 已在 `127.0.0.1:18443` 启动，等待游戏联调。诊断记录在 `logs/authorization.jsonl`。其中一次成功轮询来自原 SDK 单独进程探针。

## 11. 原版客户端联调观察（2026-11-22 10:18 后）

- 已通过当前电脑窗口确认 `hyp_beta.exe` 打开，显示原版动态登录背景。当前观察到的游戏 PID 为 `3880`，保护进程 PID 为 `11672`。实时 PID 应重新查询。
- `game/utils/debug.log` 的 `10:18:49` 记录了 CEF 导航到 `hyp.swjoy.com/oauth2/authorize.do`，随后有 TLS 握手错误 `net_error -100`。记录中的授权参数不应复制到公开输出。
- 此次游戏启动后，本机 `18443` 服务尚未收到原版游戏轮询。`authorization.jsonl` 当前仅有早先独立原 SDK 探针发出的一次请求，不能作为原版游戏消费应答的证据。
- 游戏连接中有 `106.2.95.81:8989` 的 `SynSent`，用途尚待确认。
- 已发现共同 SDK 配置 `game/netease.data` 仍使用原基础地址。本轮也将其中的 `UNISDK_JF_GAS3_URL` 改为 `http://127.0.0.1:18443/pgx/sdk/`，并将原文件备份在与 §10 相同的备份目录。此改动发生在游戏启动之后，需要重启客户端才能验证是否影响最终 SDK 选项。
- 当前 UI 控制未能触发游戏退出按钮或 Alt+F4，游戏仍显示登录背景。需要用户关闭当前游戏，重新启动 `hyp_start.exe` 并点击“进入游戏”，再观察本机轮询。此处为当前具体联调卡点。

## 12. 完成重启及修正 SDK 联调方向（2026-11-22）

- 通过标准管理员 PowerShell 辅助进程，已按完整路径匹配结束本目录的游戏、保护及 CEF 进程，并重新运行官方启动器。原版客户端重启成功，重新显示动态登录背景。§11 的“必须手动关闭游戏”已由这一步解决。
- 只读枚举重启后的游戏进程模块，发现加载了 `ntunisdkmpay.dll` 和 `mpay.dll`，未发现 `ntunisdkswjoy.dll`。因此 §9/§10 的独立顺网 SDK 消费验证目前仅证明该 DLL 的协议字段，尚不能证明游戏当前登录流程使用它。后续应以实际进程请求和加载代码为准。
- MPay 的本游戏日志和缓存位于 `%APPDATA%/Netease/Mpay/`，文件名前缀为 `aecfwtbzxmaaaamp-g-pgx`。日志内容为编码数据，`.db` 文件不能由普通 SQLite 直接读取。本轮仅检查了文件结构，未修改它们。
- `mpay.dll` 静态字符串包含 `https://service.mkey.163.com/mpay/` 及 `api/users/...` 接口。尚未证实当前 OAuth 网页导航与这些 MPay 接口的调用关系。
- 本地 HTTPS 授权候选服务另起于 `127.0.0.1:443`，参数为 `sdk_auth.py --port 443 --local-login`。原 `18443` HTTP 服务仍运行。两进程的会话各自独立，不应把它们视为共享授权状态。
- 临时 hosts 块标记为 `# BEGIN HYP SDK LOCAL AUTH` 到 `# END HYP SDK LOCAL AUTH`，将 `mgbcb.matrix.netease.com` 和 `mgbsdktest.matrix.netease.com` 指向本机。原 hosts 的逐字节备份为 §10 备份目录中的 `hosts.before-local-auth`。
- 系统代理排除列表临时添加了 `hyp.swjoy.com`、`mgbcb.matrix.netease.com`、`mgbsdktest.matrix.netease.com` 和 `game.hyp.163.com`，用于让本地授权请求直达本机。原 `ProxyOverride` 的存在状态和原值备份为同目录的 `proxy-override.before.json`。目前 `ProxyEnable` 为 `1`。
- 最新一次路由调整后已重新打开启动器，当前需从“进入游戏”继续。UI 自动点击尚未触发该入口，本机日志尚无这一轮的游戏请求。应重新查询进程和日志判断用户是否已手动进入，避免把探针请求当成游戏成功登录。

## 13. 本机收到轮询及 HTTPS 诊断修复（2026-11-22）

- 上一轮客户端随后启动，PID 为 `876`。同一启动时段的 `authorization.jsonl` 出现两次 `POST /s/chatft.s`（404）和一次 `POST /pgx/sdk/query_auth_info`（400）。原日志没有请求正文及请求来源 PID，因此尚不能据此判定 400 的具体字段原因，或修正 §12 的 SDK 加载结论。
- `sdk_auth.py` 新增失败轮询的本机正文记录 `logs/poll_<ns>.bin` 和 `POLL_REJECT` 原因事件。人工可读日志不输出授权索引及会话值。下一轮请求到达后，须先检查格式和字段类型，避免凭猜测放宽校验。
- HTTPS 握手改在各连接的工作线程中处理，增加 10 秒握手超时和 TLS 成功/错误事件，避免一个错误客户端堵住接入或握手失败没有记录。7 项接口检查通过，含错误 TLS 连接后仍可处理正常请求。独立原 SDK 消费探针复验通过。
- 普通用户方式启动 `hyp_start.exe` 两次返回 WinError 740。检查清单确认它要求管理员。`bootloader.exe` 的清单为 asInvoker，但直接启动后实际又产生了高权限的启动器进程。普通权限启动没有解决自动点击卡点。
- 已关闭上一轮测试游戏并重新打开官方启动器。目前停在“进入游戏”，需用户点击才能让新版本诊断服务收到下一条实际轮询。实机仍未进入大厅或对局。
- 本机 `443` HTTPS 和 `18443` HTTP 服务继续运行，当前 hosts、代理排除列表和 SDK 配置见 §10–§12。恢复时使用同一备份目录里的原文件和 `proxy-override.before.json`，并只移除本任务的 hosts 标记块，以保留之后的其他变更。

## 14. 查明原版请求被拒原因（2026-11-22）

- 最新客户端成功启动，`443` 日志记录了 TLS 1.2 握手，以及来自同一启动时段的 SDK 轮询。失败正文保存在 `logs/poll_1795314874278029700.bin`，共 83 字节，JSON 含字符串 `auth_index` 和 `login_channel`。
- 该索引为 **37 位十六进制字符串**，渠道为 `swjoydy`。只读解析 `game/utils/debug.log` 的最新授权 URL，回调路径内索引也是 37 位，且与正文的 `auth_index` 一致。状态参数为空字符串。
- 400 的具体原因是 `sdk_auth.py` 误将索引固定为 32 位。已去掉该假设，允许长度上限为 128 的非空十六进制索引。新增 37 位索引贯通授权与轮询的回归用例，8 项检查通过。
- 这条真实请求证明游戏启动流程确实使用了与候选服务相同的查询路径、字段名和渠道值，即使模块枚举中没有普通加载的 `ntunisdkswjoy.dll`。仍需原版游戏消费新的成功应答，才能验证完整 SDK 登录。
- 已重新加载 `443` 候选服务并再次启动官方启动器，等待下一次原版轮询，随后应继续记录登录后的游戏请求。
- 本轮曾在读取新日志前过早将目标标记为受阻。新日志随即提供了上述可修复错误，随后继续进行了实现与验证。目标尚未完成。

## 15. 本地账户与大厅 UDP 已贯通（2026-11-22 11:08）

- 原版客户端成功收到 `/pgx/sdk/query_auth_info` 的 HTTP 200 及 JSON code 200，随后实际连接本机 TCP 8766。该阶段的 `/s/chatft.s` 仍是 404，客户端依然推进到后续协议，不能继续把此端点当成已证实的登录阻断。
- 新增 tcp_probe.py 监听后抓到 187 字节请求：小端 uint32 正文长度 183、小端 uint16 方法编号 4、protobuf 字段 1 含 178 字节编码串。重连时编码值变化。
- 对客户端做只读运行时检查，在 Python 堆找到完整 3161 字节 FileDescriptorProto。已保存 protocol/login.descriptor.bin，并由 extract_login_schema.py 重建 19 个消息、2 个服务，含嵌套字段的 login.proto/login.schema.json。protocol/common.proto 是仅含 Void 的兼容声明，不是完整原始 common.proto。
- gt_protocol.py 实现客户端 gtRpcChannel 的长度/方法头和 protobuf 标量编解码；3 项检查覆盖实际报文布局、拆包/粘包和错误长度。
- login_server.py 为实验实现。--bootstrap-account 会在 FetchKey 后发送 LoginClient.UniSDKReply（方法 7），**未实现 RSA FetchKey 挑战恢复**，不应称为完整认证协议。原版实测接受了这条本地账户回复并发出 QueryUser（方法 3）。
- 第一次 QueryReply 的 UUID 错用文本，导致客户端断开。client-code-evidence.json 中 QueryReply 处理函数明确使用 UUID(bytes=...)；已改为两个 16 字节值。
- 启动器会覆盖手改的 serverlist.ini。因此 update.ini 的 ServerList 现为 http://127.0.0.1:18443/server_list.txt，SDK HTTP 服务提供 protocol/local_serverlist.ini。官方列表保留在备份目录 serverlist.before-local-login.ini。
- 11:02 原版 PID 17172 接受账户和查询回复，随后真实向本机 UDP 25000 发出 10 条 273 字节报文，证据在 logs/udp_25000_*.bin。TCP 25000 没有流量。已新增 UDP 记录，不能只看 TCP。
- mercury_protocol.py 识别这条单请求信封：flags=1、method=0、正文260字节、请求ID、next request=0、version=43、256字节载荷、尾部first request offset=2。2 项解析测试通过。其内层载荷尚未解密，大厅/BaseApp 通道和实体创建未实现。
- 已从原客户端代码确认 game.offline/initOffline 仍存在，会创建本地 space、读 universe/name、加载 universes/eg/ 下场景、构造 Avatar 并设置为 player。尚无可用调用入口；不能宣称可离线游玩。Ctrl+P/Ctrl+grave 未显示控制台。
- local_stack.py 统一启动本机 443、18443、TCP 8766，以及仅记录的 TCP/UDP 25000/25100。443/18443 共享授权会话状态。启动命令 python -B _local_server/local_stack.py；此服务不是完整可玩服务端。
- 11:06 检查发现上一轮所有服务和游戏均已退出，原因未知；已按实际端口空闲状态重新启动服务与官方启动器。以后先查活进程/监听，不能靠旧句柄断定仍运行。
- 当前目标仍未完成，验收保持原版客户端在本地地图能操作角色。
