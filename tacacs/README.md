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

构建命令：

```bash
docker compose build tacacs
```

重启命令：

```bash
docker compose up -d tacacs
```
