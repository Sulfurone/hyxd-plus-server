# 荒野行动Plus 本地服务端与历史调查

2026-11-23 当前状态：本地大厅支持单人对局和自由练习。9 人对局包含运输机投放、可转向的翼装滑翔、按地图分阶段缩圈、圈外伤害和胜负结算。启动和操作方法见 [README.repo.md](README.repo.md)。

以下内容是早期调查记录，描述的是修复前状态；其中“无法单机”“未实现 AI”等结论已被后续实现替代。

工作目录：`I:\hyxd\hyp`（客户端 3.0.43 网吧版，官方 2021-11-22 停服）

2026-11-22 11:08 更新：已实现 SDK 本地授权及实验性游戏账户回复。原版客户端已消费账户、区服查询回复，并向本机 UDP 25000 发送大厅登录包。**尚未实现大厅会话、角色创建和进图，仍不能单机游玩。** 当前实现和环境变更以 [HANDOFF.md 第 15 节](HANDOFF.md#15-本地账户与大厅-udp-已贯通2026-11-22-1108) 为准。

运行本机服务：`python -B _local_server/local_stack.py`。该命令启动 SDK 和账户实验服务，大厅端口目前只记录请求。接口检查：在 `_local_server` 目录运行 `python -B -m unittest -v test_sdk_auth test_gt_protocol test_mercury_protocol`。

---

## 一、结论

| 目标 | 可行性 | 状态 |
|---|---|---|
| 本地启动客户端，进到游戏本体的登录界面 | ✅ **已实现并验证** | 见下方截图流程 |
| 本地伪造更新服 / 服务器列表服（HTTP 层） | ✅ 可以 | `localsrv.py` |
| 本地伪造登录端点（TLS + HTTP 层） | ✅ **已实现并验证** | `tls_login.py` |
| 真正进入对局（单机打枪） | 尚未实现 | 需继续实现游戏账户验证、大厅与场景协议 |

当前最远实机验证到向本机大厅发出 UDP 登录请求。下方保留 DSH 的历史探索记录，其中对 96 字节载荷的判断尚未建立与登录失败的因果关系。SDK 授权服务的最新实现和证据以交接文档第 9 节为准。

---

## 二、完整链路（全部实测）

### 2.1 进程链
```
hyp_start.exe            requireAdministrator，启动器
 └ bootloader.exe        CEF 启动器 UI，本地 IPC :11199
    └ hypprotect.exe     反作弊壳
       └ game\hyp_beta.exe -p 1 -redirect 919552     真正的游戏
```

### 2.2 为什么原样启动会卡住

| 卡点 | 原因 | 是否可修 |
|---|---|---|
| 启动器右侧「无法安全地连接到此页面」 | 内嵌 2018 版 CEF 只支持 TLS 1.0/1.1，官网已升 TLS 1.2+ | 界面装饰，可忽略 |
| 「正在更新 0 KB/s」 | `update.ini` 写 `Version=3.0.43`，服务器最新 `3.0.103`，去拉 `patch3.0.44.pg`，该文件 404 | ✅ 改版本号可跳过 |
| 游戏内登录界面白屏 | CEF 要加载 `https://hyp.swjoy.com/oauth2/authorize.do`，该域**公网无 A 记录**（已下线） | ❌ 见第四节 |

### 2.3 启动器兼容问题（已解决）
`update.ini` 里把
```ini
Version=3.0.43
```
改成 `Version=3.0.999`（大于服务器上的 3.0.103），启动器就不进更新流程，直接显示「进入游戏」。

### 2.4 登录端点的真实协议（重要发现）
客户端登录时连接 `game.hyp.163.com:443`，**实测握手成功**：

```
TLS 1.0  /  ECDHE-RSA-AES256-SHA        ← 老协议，Python/OpenSSL 都能起
应用层：明文 HTTP/1.1

POST /s/chatft.s HTTP/1.1
Host: game.hyp.163.com
Accept: */*
Content-Length: 96
Content-Type: application/x-www-form-urlencoded

<96 字节二进制密文>
```

抓到的载荷（两次抓包**完全一致**，是固定值，不是随机 nonce）：

```
c6fa6926eef755a409c4ad8f5be1ab716324213847e9b8d80e687133a6f08835
1d8e772d32161514c1f831b1a3acf908bd34025d9d8841d6f520c0d33dbf3d23
63e96b90f445c5f98bb9c40ec7409c3f85eee7cccffb57f6cb517830f9e76d0b
```

分析结果：
- 96 字节 = 6×16 = 12×8，两种分组都对齐
- 熵 6.35 bit/byte，可打印字符占 36% → 加密或强编码
- 无 ECB 重复块、无单字节 XOR 特征
- 尾部 `0x0b` 疑似 PKCS7 padding，但整块校验不成立

**结论**：传输层可以伪造，密文层需要逆向 `hyp_beta.exe` 的加解密（VMProtect 保护，字符串全加密）。

---

## 三、本目录的工具

```
_local_server\
  start_game.bat     ★ 一键启动（跳过更新 + 清理残留 + 启动）
  restore_update.bat    还原 update.ini 版本号
  localsrv.py       更新服 + 服务器列表服（HTTP:80）+ 游戏端口抓包（TCP:25000/25100）
  tls_login.py      ★ 登录端点探针：TLS 1.0 自签证书 + 记录 /s/chatft.s 请求
  tls_tap.py        只抓 ClientHello SNI 的轻量探针
  apply_hosts.ps1   hosts 重定向（需管理员）
  restore_hosts.ps1 还原 hosts
  run_server.bat    启动 localsrv.py
  logs\             运行日志 + 抓到的请求原始字节 req_*.bin
```

### 已实测有效
- `tls_login.py` 完成 TLS 1.0 握手，客户端确实发来 `POST /s/chatft.s`，96 字节落盘为 `req_*.bin`
- `localsrv.py` 返回的 `server_list.txt` / `patch_list3.txt` 被启动器正常解析

### 注意
- `tls_login.py` 需要 openssl 生成自签证书，已配置自动寻找 `C:\Program Files\Git\usr\bin\openssl.exe`
- 客户端报「证书不受信任」也不会中断，它用的是 `verify_mode = CERT_NONE` 语义

---

## 四、为什么还是不能单机玩

### 4.1 身份校验过不去
登录界面是 **OAuth2 授权页**，客户端要加载：

```
https://hyp.swjoy.com/oauth2/authorize.do
    ?client_id=3525
    &redirect_uri=https://mgbcb.matrix.netease.com/pgx/cb/authcallback/swjoydy/pc/defaults/<32位token>
    &response_type=code
    &state=
```

实测：
- `hyp.swjoy.com` 在 Cloudflare / Google DoH 上**均无 A 记录**（子域已下线）
- 主域 `swjoy.com` 还活着（203.107.63.144），但用 `Host: hyp.swjoy.com` 访问返回 **502**
- 就算本地伪造授权页，还得伪造 `mgbcb.matrix.netease.com` 的回调，且 token 要与 96 字节密文对得上

### 4.2 96 字节密文解不开
`hyp_beta.exe` 受 VMProtect 保护，扫不到任何明文协议字段（`login`/`gate`/`hall`/`token` 出现次数全为 0）。要解密必须动态调试 —— 而 `hypprotect.exe` 反作弊会阻断调试器。

### 4.3 资源包也是加密的
`res\*.pg` 全部以魔数 `79 74 67 70`（"ytgp"）开头，之后即为密文，无明文目录表。场景包 `universes\eg\hukou.pg` 达 1.2 GB。

---

## 五、你现在能做的

### 方案 A：玩到登录界面（已实现）
双击 **`_local_server\start_game.bat`**，然后：
1. 忽略启动器右侧的「无法安全地连接到此页面」
2. 点右下角 **进入游戏**
3. 游戏内 EULA 点 **同意**
4. 停在登录界面（白板 + LOGO）—— 到这就结束了

想恢复原版本号：双击 `restore_update.bat`。

### 方案 B：继续逆向（如果你有基础）
已备好的切入点：
- **登录端点**：`game.hyp.163.com:443`，TLS 1.0 + HTTP，`tls_login.py` 已能捕获请求
- **96 字节载荷**：固定值，说明不含随机数，加密方式大概率是 `密钥 + 固定 IV` 或纯 ECB 变体
- **serverlist.ini**：根目录明文可改，大厅地址随意指定
- **游戏参数**：`hyp_beta.exe -p 1 -redirect 919552`，可试参数组合

下一步最有效的是**动态 dump `hyp_beta.exe` 解密后的内存**（VMProtect 会在运行时解壳），但需要先绕过 `hypprotect.exe`。

### 方案 C：换个思路
纯粹想在本地打枪的话，用 UE + FPS 模板自己拼一个，比逆向这套快得多。`res` 里的美术资源（`.pg` 需解密）可以考虑复用。

---

## 六、改动清单（当前已全部还原）

| 文件 | 处理 | 现状 |
|---|---|---|
| `C:\Windows\System32\drivers\etc\hosts` | 曾加重定向 | **已删除，0 条** |
| `I:\hyxd\hyp\update.ini` | 曾改 `3.0.999` | **已还原为 3.0.43** |
| `I:\hyxd\hyp\serverlist.ini` | 曾指向 127.0.0.1 | **已还原为官方列表** |

> 注意：你路由器把 `game.hyp.163.com`、`pgx.gph.netease.com` 等解析到 127.0.0.1
> （可能是 UU 加速器的本地代理），这不是我改的，也不影响本次结论 ——
> 因为这些域在公网本来就没有 A 记录了。
