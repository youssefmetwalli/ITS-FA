# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set environment variables to prevent Python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory to /app
WORKDIR /app

# Install system dependencies (if your app or dependencies require them, e.g. gcc for compiled packages)
RUN apt-get update && apt-get install -y gcc

# Copy requirements.txt into the container at /app/
COPY requirements.txt /app/

# Upgrade pip and install Python dependencies
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the current directory contents into the container at /app/
COPY . /app/

# (Optional) Expose a port (Vercel routes your container via HTTP)
EXPOSE 3000

# Define the command to run your app using gunicorn.
# This assumes your api/index.py exports a WSGI app named "handler".
CMD ["gunicorn", "--bind", "0.0.0.0:3000", "api.index:handler"]
