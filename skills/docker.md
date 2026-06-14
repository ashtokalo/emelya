---
description: Docker — containers, images, compose, debugging
---
# Docker

## Building and running

- `docker build -t <name> .` — build image from Dockerfile in current dir
- `docker run -d --name <ctr> -p 8080:80 <image>` — run detached with port mapping
- `docker run --rm -it <image> bash` — interactive one-off container
- `docker run -v $(pwd):/app <image>` — mount current dir

## Managing containers

- `docker ps` — running containers
- `docker ps -a` — all containers (including stopped)
- `docker stop <ctr>` — graceful stop
- `docker rm <ctr>` — remove container
- `docker logs <ctr>` — view stdout/stderr
- `docker logs -f --tail 50 <ctr>` — follow last 50 lines

## Inspecting and debugging

- `docker exec -it <ctr> bash` — shell into running container
- `docker inspect <ctr>` — full container details (JSON)
- `docker stats` — live resource usage
- `docker top <ctr>` — processes inside container

## Images

- `docker images` — list local images
- `docker rmi <image>` — remove image
- `docker system prune` — clean up unused data (ask user before running)

## Docker Compose

- `docker compose up -d` — start all services detached
- `docker compose down` — stop and remove containers
- `docker compose logs -f <service>` — follow service logs
- `docker compose ps` — service status
- `docker compose exec <service> bash` — shell into service
- `docker compose build` — rebuild images

Check for `compose.yaml`, `docker-compose.yml`, or `docker-compose.yaml` with read tool. Use bash tool for all commands.