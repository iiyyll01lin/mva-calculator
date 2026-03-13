# 在服务器上构建和部署 (Step-by-step)

本指南适用于在你的 Linux 服务器上直接构建 Docker 镜像并运行。

## 前置条件
- 服务器已安装 Docker
- 你有服务器 SSH 访问权限或 Git push 权限
- 服务器在公司网络内，可能也需要配置代理

---

## 方案 A: Via Git (推荐)

### 1. 本地：提交并推送代码到仓库
```bash
cd /mnt/d/workspace/line-balance
git add demo/ddm/
git commit -m "Add Docker deployment for DDM demo"
git push origin main  # 或你的目标分支
```

### 2. 服务器：登录并克隆/更新仓库
```bash
ssh user@your-server-host

# 克隆（首次）或进入现有目录
git clone https://your-repo-url.git line-balance
cd line-balance/demo/ddm

# 或如果已有本地仓库，拉取最新
cd ~/line-balance
git pull origin main
cd demo/ddm
```

### 3. 服务器：配置代理（如果公司网络需要）
如果服务器也在公司网络内，先配置 Docker daemon 代理：
```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/proxy.conf >/dev/null <<'EOF'
[Service]
Environment="HTTP_PROXY=http://10.6.254.210:3128"
Environment="HTTPS_PROXY=http://10.6.254.210:3128"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker

# 验证
docker pull nginx:1.25-alpine
```

### 4. 服务器：检查 `.env` 中的代理配置
```bash
cat demo/ddm/.env
```
如果需要修改，编辑 `.env`：
```bash
nano demo/ddm/.env
# 或
vi demo/ddm/.env
```

### 5. 服务器：构建并运行
```bash
cd demo/ddm

# 使用 docker-compose（推荐）
docker compose up -d --build

# 或手动构建
docker build -t ddm-demo:latest .
docker run -d --name ddm-demo -p 8080:80 ddm-demo:latest
```

### 6. 验证部署
```bash
# 检查容器状态
docker ps | grep ddm-demo

# 查看日志
docker logs ddm-demo

# 测试 HTTP 连接（服务器本地）
curl -I http://localhost:8080
```

### 7. 从本地访问
打开浏览器访问服务器：
```
http://your-server-host:8080
```

---

## 方案 B: Via SCP (单次部署)

如果不用 Git，直接复制文件到服务器：

### 1. 本地：复制 demo 文件夹到服务器
```bash
# 从本地
scp -r demo/ddm user@your-server-host:~/ddm-demo
```

### 2. 服务器：进入文件夹
```bash
ssh user@your-server-host
cd ~/ddm-demo
```

### 3. 后续步骤同方案 A（从第 3 步开始）

---

## 方案 C: 使用 Python 快速测试（不需要 Docker）

如果 Docker 在服务器上也有问题，可以用 Python 快速验证：

### 服务器：
```bash
cd ~/ddm-demo  # 或你复制到的位置
python3 -m http.server 8080
```

然后访问 `http://your-server-host:8080` 验证 HTML 和模块加载。

---

## 后续：更新部署

### 如果使用 Git（方案 A）
每次更新后：
```bash
cd ~/line-balance
git pull origin main
cd demo/ddm
docker compose up -d --build
```

### 如果使用 SCP（方案 B）
每次更新后：
```bash
# 本地
scp -r demo/ddm user@your-server-host:~/ddm-demo-new

# 服务器
rm -rf ~/ddm-demo
mv ~/ddm-demo-new ~/ddm-demo
cd ~/ddm-demo
docker compose up -d --build
```

---

## 故障排除

### 容器无法启动
```bash
# 查看错误日志
docker logs ddm-demo

# 检查端口占用
sudo netstat -tlnp | grep 8080
# 或
sudo lsof -i :8080

# 强制停止并删除旧容器
docker stop ddm-demo
docker rm ddm-demo
docker compose up -d --build
```

### 无法访问服务
- 检查防火墙：`sudo ufw allow 8080`
- 检查 Nginx 配置：`docker exec ddm-demo cat /etc/nginx/conf.d/default.conf`
- 检查模块是否存在：`docker exec ddm-demo ls -la /usr/share/nginx/html/mva/`

### 构建失败
- 重新配置 Docker daemon 代理（见第 3 步）
- 查看完整构建日志：`docker compose up --build` (不加 `-d`)

---

## 补充：配置开机自启（可选）

在服务器上，让容器开机自启：
```bash
cd ~/line-balance/demo/ddm  # 或你的路径
docker compose up -d --build

# 此后容器在服务器重启时自动启动
# （因为 docker-compose.yml 中设置了 restart: unless-stopped）
```

或手动配置：
```bash
docker run -d \
  --restart unless-stopped \
  --name ddm-demo \
  -p 8080:80 \
  ddm-demo:latest
```
