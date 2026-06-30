FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV LLM_PROVIDER=gemini
ENV GEMINI_MODEL=gemini-2.5-flash
ENV LLM_TEMPERATURE=0
ENV DEPLOYMENT_ENVIRONMENT=container
ENV SERVICE_NAME=governed-refund-agent
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_PORT=8501

EXPOSE 8501

CMD ["streamlit", "run", "src/streamlit_app.py"]
