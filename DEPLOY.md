# Deploying the Regina Web UI
>[toc]

`regina_web.py` is a single Python process (standard library HTTP server) that serves the demo page and streams Regina's delegations over Server-Sent Events. It is built for demos, not production traffic: **one instance, one conversation, one request at a time**, with the chat history held in memory.
Pick the option that matches how long the demo needs to live:
| Option | Time to URL | Good for | HTTPS |
| --- | --- | --- | --- |
| [Tunnel from your laptop](#option-1-tunnel-from-your-laptop) | ~2 min | a live call or meeting today | yes (tunnel provides it) |
| [Docker on a VM](#option-2-docker-on-any-host) | ~15 min | a demo URL that lasts days or weeks | via Caddy |
| [AWS App Runner](#option-3-aws-app-runner) | ~30 min | a managed URL in an AWS account | yes (built in) |


## Before you deploy

### Checklist
---
- **Set a password.** Any host other than `127.0.0.1` without `REGINA_WEB_PASSWORD` is open to whoever finds the URL. In live mode that means they spend your Anthropic credits. The server prints a warning when this happens.
- **Choose the mode.** `mock` needs no key and never calls an API; it is the safe default for a public URL. `live` needs `ANTHROPIC_API_KEY`, stored as a platform secret, never baked into the image.
- **Run exactly one instance.** The conversation lives in process memory. Two instances behind a load balancer would split one chat across two Reginas.
- **Allow long requests.** A live briefing streams for as long as Regina and her agents take (often tens of seconds). The proxy in front must allow that and must not buffer `text/event-stream` responses.
- **Expect resets.** A restart or redeploy forgets the conversation. That's fine for a demo; there is no database.

### Configuration
---
Everything is an environment variable. Nothing is required for a mock-mode demo.
| Variable | Default | Purpose |
| --- | --- | --- |
| `REGINA_WEB_PASSWORD` | _(empty = no password)_ | Enables HTTP Basic auth on every route except `/healthz`. Any username works. |
| `ANTHROPIC_API_KEY` | _(empty)_ | Enables live mode. Store as a secret. |
| `REGINA_MODE` | `auto` | `auto` (live when a key exists), `mock`, or `live`. |
| `REGINA_WEB_HOST` | `127.0.0.1` (`0.0.0.0` in the image) | Bind address. |
| `PORT` | `8000` | Listen port. Most PaaS hosts set this for you. |
| `REGINA_MODEL` / `REGINA_SUBAGENT_MODEL` | `claude-opus-5` / `claude-sonnet-5` | Orchestrator and sub-agent models in live mode. |
| `REGINA_SUBAGENTS` | `mock` | `llm` makes each sub-agent a Claude call too. |
| `REGINA_USER_NAME` | `Your Majesty` | How Regina addresses the audience. |
| `REGINA_TODAY` | _(real date)_ | Pin the demo date (`YYYY-MM-DD`) for a reproducible script. |

### Endpoints
---
- `GET /healthz`: _always open, returns `ok`; point health checks here_
- `GET /`: _the page_
- `GET /api/info`, `GET /api/ask?q=…`, `GET /api/briefing`, `POST /api/reset`: _the app API (SSE for ask/briefing)_


## Option 1: Tunnel from your laptop

### Run locally, share through ngrok or Cloudflare
---
Fastest path for a meeting. The app runs on your machine; the tunnel gives it a public HTTPS URL that disappears when you stop it.
```bash
# Terminal 1: the app (mock mode, password on)
source .venv/bin/activate
REGINA_WEB_PASSWORD='pick-a-demo-password' python regina_web.py --mock

# Terminal 2: one of these
ngrok http 8000                                   # brew install ngrok; needs a free ngrok account
cloudflared tunnel --url http://127.0.0.1:8000    # brew install cloudflared; no account for quick tunnels
```
Open the `https://…` URL the tunnel prints, enter any username plus the password, and run the briefing.
- The server keeps binding to `127.0.0.1`. Only the tunnel can reach it, not your whole network.
- For live mode, drop `--mock` and export `ANTHROPIC_API_KEY` first. Keep the password on.
- Your laptop must stay awake and online for the whole demo.


## Option 2: Docker on any host

### Build and try the image locally
---
The `Dockerfile` installs only what Regina needs (`anthropic`, `python-dotenv`), runs as a non-root user, binds `0.0.0.0:8000`, and has a health check against `/healthz`.
```bash
docker build -t regina-web .
docker run --rm -p 8000:8000 \
  -e REGINA_MODE=mock \
  -e REGINA_WEB_PASSWORD='pick-a-demo-password' \
  regina-web
# → http://localhost:8000
```
Live mode: pass the key from your shell or an env file, never as a literal in shell history or the image.
```bash
docker run --rm -p 8000:8000 --env-file .env -e REGINA_WEB_PASSWORD='pick-a-demo-password' regina-web
```

### Run on a VM (EC2, Lightsail, any VPS)
---
On an Apple Silicon Mac, build for the VM's CPU (most cloud VMs are `amd64`). Otherwise the container fails with `exec format error`.
```bash
# On your laptop: build for amd64 and copy to the VM (or push to a registry and pull there)
docker buildx build --platform linux/amd64 -t regina-web:amd64 --load .
docker save regina-web:amd64 | gzip | ssh ubuntu@YOUR_VM_IP 'gunzip | docker load'
```
On the VM, create `/opt/regina/.env` (mode `600`) with `REGINA_WEB_PASSWORD`, and `ANTHROPIC_API_KEY` if live. Then:
```bash
docker run -d --name regina --restart unless-stopped \
  -p 127.0.0.1:8000:8000 --env-file /opt/regina/.env \
  regina-web:amd64
```
Put Caddy in front for automatic HTTPS. Point a DNS `A` record at the VM and open ports 80/443 (not 8000) in the firewall or security group.
```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
regina.example.com {
    reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl reload caddy
```
Caddy streams `text/event-stream` responses without buffering by default. If you use nginx instead, the app already sends `X-Accel-Buffering: no`; also set `proxy_read_timeout 300s;`.


## Option 3: AWS App Runner

### Notes before you start
---
- App Runner terminates TLS, gives you an `https://…awsapprunner.com` URL, and restarts unhealthy containers.
- **120-second request limit.** App Runner closes any HTTP request after 120 s, and a streamed answer counts as one request. Mock mode and normal live questions fit. A slow live briefing that runs past 120 s shows "Lost connection to the server" in the chat.
- **Pin it to one instance.** App Runner autoscales by default; the auto scaling configuration below caps it at 1.
- `PORT` is reserved by App Runner. Set the service port to `8000` and don't pass `PORT` as an environment variable.
- Set `AWS_REGION` below to your region; `ACCOUNT_ID` is read from your CLI credentials. Run all commands in one shell session so the variables carry over.

### Push the image to ECR
---
```bash
export AWS_REGION=us-east-1 ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export REPO=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/regina-web

aws ecr create-repository --repository-name regina-web
aws ecr get-login-password | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com
docker buildx build --platform linux/amd64 -t $REPO:latest --push .
```

### Store secrets
---
```bash
aws secretsmanager create-secret --name regina/web-password --secret-string 'pick-a-demo-password'
# Live mode only:
aws secretsmanager create-secret --name regina/anthropic-api-key --secret-string 'your_api_key_here'
```

### Create the IAM roles
---
Two roles: an **access role** lets App Runner pull from ECR, and an **instance role** lets the running container read the secrets.
```bash
# Access role (ECR pull)
aws iam create-role --role-name regina-apprunner-ecr-access --assume-role-policy-document '{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "build.apprunner.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}'
aws iam attach-role-policy --role-name regina-apprunner-ecr-access \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess

# Instance role (read only Regina's secrets)
aws iam create-role --role-name regina-apprunner-instance --assume-role-policy-document '{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "tasks.apprunner.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}'
aws iam put-role-policy --role-name regina-apprunner-instance --policy-name read-regina-secrets --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{\"Effect\": \"Allow\", \"Action\": \"secretsmanager:GetSecretValue\",
    \"Resource\": \"arn:aws:secretsmanager:$AWS_REGION:$ACCOUNT_ID:secret:regina/*\"}]
}"
```

### Create the service
---
```bash
SCALING_ARN=$(aws apprunner create-auto-scaling-configuration \
  --auto-scaling-configuration-name regina-single --min-size 1 --max-size 1 \
  --query AutoScalingConfiguration.AutoScalingConfigurationArn --output text)
PASSWORD_ARN=$(aws secretsmanager describe-secret --secret-id regina/web-password --query ARN --output text)

aws apprunner create-service --service-name regina-web \
  --auto-scaling-configuration-arn "$SCALING_ARN" \
  --health-check-configuration Protocol=HTTP,Path=/healthz \
  --instance-configuration "Cpu=0.25 vCPU,Memory=0.5 GB,InstanceRoleArn=arn:aws:iam::$ACCOUNT_ID:role/regina-apprunner-instance" \
  --source-configuration "{
    \"AuthenticationConfiguration\": {\"AccessRoleArn\": \"arn:aws:iam::$ACCOUNT_ID:role/regina-apprunner-ecr-access\"},
    \"AutoDeploymentsEnabled\": false,
    \"ImageRepository\": {
      \"ImageIdentifier\": \"$REPO:latest\",
      \"ImageRepositoryType\": \"ECR\",
      \"ImageConfiguration\": {
        \"Port\": \"8000\",
        \"RuntimeEnvironmentVariables\": {\"REGINA_MODE\": \"mock\"},
        \"RuntimeEnvironmentSecrets\": {\"REGINA_WEB_PASSWORD\": \"$PASSWORD_ARN\"}
      }
    }
  }"
```
For live mode, set `REGINA_MODE` to `live` and add `"ANTHROPIC_API_KEY": "<anthropic-api-key secret ARN>"` to `RuntimeEnvironmentSecrets`. Consider `Cpu=1 vCPU,Memory=2 GB` so the thread pool isn't throttled.
Get the URL once the status is `RUNNING` (a few minutes):
```bash
aws apprunner list-services --query "ServiceSummaryList[?ServiceName=='regina-web'].[Status,ServiceUrl]" --output text
```

### Update and tear down
---
```bash
SERVICE_ARN=$(aws apprunner list-services --query "ServiceSummaryList[?ServiceName=='regina-web'].ServiceArn" --output text)

# Ship a new build
docker buildx build --platform linux/amd64 -t $REPO:latest --push .
aws apprunner start-deployment --service-arn "$SERVICE_ARN"

# Remove everything when the demo is over (App Runner bills while the service exists)
aws apprunner delete-service --service-arn "$SERVICE_ARN"
aws apprunner delete-auto-scaling-configuration --auto-scaling-configuration-arn "$SCALING_ARN"
aws ecr delete-repository --repository-name regina-web --force
aws secretsmanager delete-secret --secret-id regina/web-password --force-delete-without-recovery
```

### Other container platforms
---
Render, Railway, Fly.io, Cloud Run and Azure Container Apps all work the same way: deploy the `Dockerfile`, let the platform set `PORT`, use `/healthz` for the health check, cap instances at 1, set `REGINA_WEB_PASSWORD` (and `ANTHROPIC_API_KEY` for live) as secrets, and check the platform's request timeout against how long your live briefing takes.


## Verify the deployment

### Smoke test
---
```bash
URL=https://your-demo-url
curl -s -o /dev/null -w '%{http_code}\n' $URL/healthz              # 200
curl -s -o /dev/null -w '%{http_code}\n' $URL/                     # 401 (password on)
curl -s -u demo:'pick-a-demo-password' $URL/api/info               # {"mode": "mock", ...}
curl -sN -u demo:'pick-a-demo-password' "$URL/api/ask?q=Do%20I%20have%20any%20conflicts%20today%3F"
```
The last command should print `data: {"kind": "fan_out" …}`, then `delegate`, `reply`, `synthesize` and `answer` lines **one at a time**. In the browser, check the header badge: `mock · offline` or `live · <model>`.


## Troubleshooting

### Common issues
---
- **The trace and answer appear all at once, after a long pause.** _A proxy is buffering the event stream. Caddy and App Runner don't; for nginx, keep `X-Accel-Buffering: no` and add `proxy_buffering off;`._
- **"Lost connection to the server" partway through a live answer.** _The platform's request timeout fired (App Runner: 120 s). Use mock mode for the briefing, `REGINA_SUBAGENTS=mock`, or a host with a longer timeout._
- **The badge says `mock` but you wanted live.** _`ANTHROPIC_API_KEY` didn't reach the container. With `REGINA_MODE=auto`, a missing key silently means mock. Set `REGINA_MODE=live` to fail loudly._
- **`AuthenticationError` shown in the chat.** _Live mode with an invalid or revoked key. Rotate the secret and redeploy._
- **The browser keeps asking for the password.** _Wrong password, or a secret with a trailing newline. Recreate it with `--secret-string` rather than from a file._
- **`exec format error` in the container logs.** _Image built on Apple Silicon for `arm64`. Rebuild with `--platform linux/amd64`._
- **Clicks seem ignored while someone else is using it.** _By design, requests queue one at a time on a single shared conversation. Give each audience its own deployment._
- **Health check fails right after start.** _The service port isn't `8000`, or `REGINA_WEB_HOST` was overridden to `127.0.0.1` inside the container._

- [AWS Docs: Developing application code for App Runner](https://docs.aws.amazon.com/apprunner/latest/dg/develop.html): _Runtime constraints, including the 120-second request timeout and stateless instances_
- [AWS Docs: Referencing environment variables in App Runner](https://docs.aws.amazon.com/apprunner/latest/dg/env-variable.html): _Secrets Manager references and the reserved `PORT` variable_
- [Caddy Docs: reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy): _Streaming and flush behavior for proxied responses_
- [ngrok Docs](https://ngrok.com/docs): _Exposing a local port over HTTPS with `ngrok http`_
- [Cloudflare Docs: Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/): _`cloudflared` and account-free quick tunnels_
