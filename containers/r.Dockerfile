# agent_pool/agents/r_processor/Dockerfile
FROM rocker/r-ver:4.5.2

# 1) Create an unprivileged group & user (UID:GID=1000)
RUN groupadd --gid 1000 agentgroup && \
    useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin agentuser

# 2) Install any system deps needed by CRAN packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    libcurl4-openssl-dev libssl-dev libxml2-dev \
    && rm -rf /var/lib/apt/lists/*

# 3) Install your R libraries
RUN R -e "install.packages(c('tidyr','dplyr','ltm','jsonlite'), repos='https://cran.rstudio.com/')"

# 4) Work as agentuser
USER agentuser
WORKDIR /home/agentuser

# 5) Nothing else to copy — the generic Python agent
#    will mount & invoke `Rscript -e "<script>"`
ENTRYPOINT []
