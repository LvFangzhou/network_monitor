# TACACS+ 镜像

本目录用于构建系统内置的 TACACS+ 容器镜像。

`docker-compose.yml` 中的 `tacacs` 服务会使用 `tacacs/Dockerfile` 构建镜像，并保留镜像名：

```text
my_build_tacacs:latest
```

运行时挂载：

| 容器路径 | 用途 |
| --- | --- |
| `/etc/tacacs+/tac_plus.cfg` | TACACS+ 配置文件 |
| `/var/log/tacacs+` | TACACS+ 日志目录 |

配置文件由系统的 Tacacs 管理页面生成，默认保存在 `${DATA_ROOT}/tacacs/tac_plus.cfg`。

构建镜像：

```bash
docker compose build tacacs
```

启动或重建容器：

```bash
docker compose up -d tacacs
```

只重启容器：

```bash
docker compose restart tacacs
```

查看日志：

```bash
docker compose logs -f tacacs
tail -f /opt/network_monitor_data/tacacs/logs/tacacs.log
```

配置变更说明：

- 修改账号、组、Key、命令权限后，需要重启 TACACS+ 容器生效。
- 操作日志依赖交换机侧开启 command accounting。
- 设备认证失败时，优先检查交换机 key、服务器 49 端口、`tac_plus.cfg` 是否生成、`nm-tacacs` 是否运行。
