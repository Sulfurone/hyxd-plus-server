# 荒野行动 Plus 服务端

针对《荒野行动 Plus》3.0.43 客户端的本地实验服务端。仓库包含 SDK 授权、账户服务、大厅 UDP、BaseApp 通道协议、资源解码器和相应测试。

## 当前状态

- 客户端可启动到登录界面，原版客户端已连接过本机 SDK、账户和大厅端口。
- 大厅登录包解析、BaseApp 登录、实体初始化时机和地图映射已有实验实现。
- 原版客户端进入地图并响应角色移动尚未验证，因此项目尚不支持完整单机游玩。

## 启动

安装 Python 3 和 `requirements.txt` 中的依赖后运行：

```powershell
python -B local_stack.py
```

服务监听 `127.0.0.1:443`、`18443`、`8766`、`25000` 和 `25100`。大厅与 BaseApp 的实体初始化依赖本机运行中的客户端会话信息，属于实验性实现。

## 测试

```powershell
python -B -m unittest -v test_sdk_auth test_gt_protocol test_mercury_protocol test_mercury_cipher test_mercury_server
```

更详细的历史调查记录见 `HANDOFF.md` 和 `README.md`。仓库不包含游戏客户端、官方资源文件或抓包日志。
