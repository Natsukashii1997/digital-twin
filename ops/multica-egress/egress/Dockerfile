# 白名单出口代理（#172）：Debian bookworm 官方签名的 squid 包，基础镜像与执行器相同（本地已有，不从 Docker Hub 另拉）
FROM node:22-bookworm-slim
ARG HTTPS_PROXY
ARG HTTP_PROXY
RUN apt-get update \
 && apt-get install -y --no-install-recommends squid \
 && rm -rf /var/lib/apt/lists/* /etc/squid/conf.d
COPY squid.conf /etc/squid/squid.conf
COPY allowed_domains.txt /etc/squid/allowed_domains.txt
# 构建期代理只给 apt 用，不留在运行环境里
ENV HTTPS_PROXY= HTTP_PROXY= https_proxy= http_proxy=
USER proxy
EXPOSE 3128
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
