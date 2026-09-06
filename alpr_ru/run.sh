#!/bin/sh
set -e

mkdir -p /data
exec uvicorn app.main:app --host 0.0.0.0 --port 8099 --proxy-headers --forwarded-allow-ips="*"
