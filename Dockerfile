# Optional: run anywhere Docker is available (Hugging Face Spaces, Render, Fly.io...).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV BCRA_DB_PATH=/tmp/bc_research.db
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
