FROM searxng/searxng:latest

# Copy our custom settings which explicitly enable the JSON API plugin
COPY settings.yml /etc/searxng/settings.yml

# Hugging face runs containers as a non-root user. 
# SearXNG needs to be able to read this file and potentially write to its cache
USER root
RUN chown -R searxng:searxng /etc/searxng/settings.yml
USER searxng

# Expose standard Hugging Face Space port
EXPOSE 7860
