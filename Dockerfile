FROM node:22-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 python3-pip git curl ripgrep tini && \
    rm -rf /var/lib/apt/lists/*

# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*

# Install Alibaba's Qwen Code CLI from npm
RUN npm install -g @qwen-code/qwen-code

WORKDIR /app

# superpos-agent-core is pulled directly from GitHub via requirements.txt
# (the `git+https://…` line), so no parent-directory build context required.
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY src/ /app/src/
COPY entrypoint.sh /app/entrypoint.sh
COPY workspace/ /workspace/

# Symlink module scripts onto PATH so Qwen can call them by name
RUN mkdir -p /workspace/.qwen/modules-bin && \
    if [ -d /workspace/.qwen/modules ]; then \
      for dir in /workspace/.qwen/modules/*/scripts; do \
        if [ -d "$dir" ]; then \
          for script in "$dir"/*; do \
            chmod +x "$script" && \
            ln -sf "$script" /workspace/.qwen/modules-bin/$(basename "$script"); \
          done; \
        fi; \
      done; \
    fi
ENV PATH="/workspace/.qwen/modules-bin:$PATH"

# Create non-root user
RUN useradd -m -s /bin/bash -u 1001 agent && \
    mkdir -p /home/agent/.qwen && \
    chown -R agent:agent /workspace /home/agent/.qwen

ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED=1
ENV HOME="/home/agent"

VOLUME ["/home/agent/.qwen"]

USER agent
WORKDIR /workspace

# tini reaps orphaned grandchildren (npm/node subprocesses left behind when
# a qwen run dies) — without it they accumulate as zombies because Python
# doesn't reap reparented orphans.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
CMD ["python3", "-m", "superpos_agent_qwen"]
