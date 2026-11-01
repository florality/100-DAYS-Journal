# 一百个今天 · Vercel Ready MVP

一个低压力记录「100 件小事」的单用户全栈 MVP。用户可以自己选第一件事，完成后留下照片、文字、颜色和余温，再根据反馈获得下一组推荐。

它不做连续打卡，也不把完成数量变成压力。当前版本已经包含 Vercel 部署所需的 Postgres、Blob 图片存储和访问密码保护。

## 已实现

- 8 个主题、48 件精选小事，每件都有最小版、标准版和冒险版
- 六种当下意图、三档电量，低电量优先推荐短时低难度任务
- 自选第一件事、完成记录、照片、文字、余温、颜色、私密标记
- 根据意图、电量、正向反馈、主题分布和跳过历史进行规则推荐
- 记忆墙、单条记忆详情、自定义任务
- 可修改目标数量和可用时长，历史记录不会被删除
- 照片在浏览器端压缩，服务端再次校正方向并移除 EXIF
- 密码保护、登录状态、退出登录
- 本地使用 SQLite；Vercel 使用 Postgres + Vercel Blob

## 本地运行

```bash
cd /Users/fangfang/Documents/Codex/2026-11-01/ban/outputs/yibai-mvp
python3 -m pip install -r requirements.txt
./start.sh
```

打开 `http://127.0.0.1:8765`。

本地默认不需要密码，数据保存在：

- `data/yibai.db`：SQLite 数据库
- `media/`：压缩后的本地照片

如果也想在本地测试密码保护：

```bash
APP_PASSWORD='换成一个强密码' SESSION_SECRET='换成一串长随机值' ./start.sh
```

## 部署到 Vercel

项目目录就是：

```text
/Users/fangfang/Documents/Codex/2026-11-01/ban/outputs/yibai-mvp
```

把这个目录推到 GitHub 后在 Vercel 导入，或者用 Vercel CLI 从该目录部署。如果仓库根目录是 `ban`，在 Vercel 项目设置里把 **Root Directory** 设为：

```text
outputs/yibai-mvp
```

Vercel 的 Serverless 文件系统不适合保存 SQLite 和上传图片，所以线上必须完成下面两步存储配置。

### 1. 连接 Postgres

在 Vercel 项目的 **Storage** 中创建一个 Neon/Postgres 数据库，并连接到当前项目。

确认项目环境变量里存在以下任意一个：

```text
DATABASE_URL
POSTGRES_URL
```

代码会优先读取 `DATABASE_URL`，没有时再读取 `POSTGRES_URL`。连接串请直接使用服务商提供的值，通常需要包含 `sslmode=require`。

### 2. 连接 Vercel Blob

在 **Storage** 中创建一个 Blob Store，并连接到当前项目。连接后应自动注入：

```text
BLOB_READ_WRITE_TOKEN
```

没有 Blob 时，文字记录仍可保存，但上传照片会返回配置错误。

### 3. 设置环境变量

在 Vercel 项目 **Settings → Environment Variables** 中添加：

| 变量 | 是否必须 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 线上必须 | Neon/Postgres 连接串，也可使用 `POSTGRES_URL` |
| `BLOB_READ_WRITE_TOKEN` | 上传照片必须 | Vercel Blob 写入令牌 |
| `APP_PASSWORD` | 线上必须 | 你的访问密码，建议至少 16 位且不要复用 |
| `SESSION_SECRET` | 强烈建议 | 登录 Cookie 签名密钥，可用 `openssl rand -hex 32` 生成 |

线上即使忘记设置 `APP_PASSWORD`，应用也不会把数据公开，而是会锁定在登录页并提示配置缺失。

设置环境变量后重新部署一次，让新变量进入运行环境。

### 4. 验证部署

打开：

```text
https://你的域名/api/health
```

正常时应看到类似：

```json
{
  "ok": true,
  "tasks": 48,
  "storage": "postgres",
  "blob": true,
  "auth_required": true,
  "auth_configured": true
}
```

重点检查：

- `storage` 是 `postgres`
- `blob` 是 `true`
- `auth_configured` 是 `true`

然后打开首页，输入 `APP_PASSWORD` 登录。

## 项目结构

```text
yibai-mvp/
├── api/index.py          # Vercel Python 函数入口
├── backend/              # FastAPI、数据库、推荐逻辑、任务数据
├── public/               # 静态页面、样式、前端脚本
├── data/                 # 本地 SQLite，不部署
├── media/                # 本地照片，不部署
├── vercel.json           # API 和静态资源路由
├── requirements.txt
├── .env.example
└── start.sh              # 本地启动
```

`vercel.json` 将 `/api/*` 交给 FastAPI，`public/**` 作为静态资源；`data/`、`media/`、`.env` 和缓存文件都不会上传。

## 数据、安全与隐私

- 当前是单用户版本，用一个共享访问密码保护数据，不是多账号系统
- 除健康检查、会话检查和登录接口外，所有 `/api/*` 都需要登录
- 登录 Cookie 为 HttpOnly、SameSite=Lax，线上使用 Secure Cookie，默认有效期 30 天
- 照片默认标记为私密；源图上限 15MB，浏览器压到 1600px 内，服务端重新编码为 JPEG 并移除 EXIF
- Vercel Blob 使用随机不可猜测的文件名；这能防止随手访问，但还不是账号级的文件权限隔离
- 如果未来开放公开分享、社区或多人使用，需要补上账号、权限、举报、审核、未成年人保护和数据删除机制

## 主要接口

| 接口 | 用途 |
| --- | --- |
| `GET /api/health` | 部署健康检查，不需要登录 |
| `GET /api/session` | 查询是否需要登录 |
| `POST /api/login` / `POST /api/logout` | 登录与退出 |
| `GET /api/bootstrap` | 首页数据、当前任务、统计和建议 |
| `GET /api/tasks` | 获取候选任务，支持换一组 |
| `POST /api/select` | 选定任务和版本 |
| `POST /api/complete` | 保存完成记录、照片、反馈和下一组推荐 |
| `POST /api/skip/:id` | 这次先不做 |
| `POST /api/custom-task` | 创建自定义任务 |
| `POST /api/profile` | 修改称呼、目标数量和时长 |
| `GET /api/memories` | 记忆墙 |
| `POST /api/demo/seed` / `POST /api/demo/reset` | 示例数据与清空 |

## 常见问题

**`/api/health` 返回 500**

线上缺少 `DATABASE_URL` 或 `POSTGRES_URL`。连接数据库后重新部署。

**登录页提示访问密码尚未配置**

在 Vercel 环境变量中设置 `APP_PASSWORD`，然后重新部署。

**照片上传提示存储未配置**

项目没有连接 Blob，或缺少 `BLOB_READ_WRITE_TOKEN`。连接 Blob Store 后重新部署。

**部署后还是本地数据**

线上不会读取 `data/yibai.db`。Vercel 版本只使用 Postgres，本地 SQLite 和线上数据彼此独立。

## 边界

这个产品是记录和陪伴工具，不是心理治疗，也不能替代专业帮助。如果用户正处在危机中，应联系当地紧急服务、心理援助热线或一个可信任的人。
