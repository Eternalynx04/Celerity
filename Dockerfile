FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && useradd --create-home labuser
COPY tests ./tests
COPY scripts/verify_phase1.py ./scripts/verify_phase1.py
USER labuser
EXPOSE 8000
CMD ["uvicorn", "celerity.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

