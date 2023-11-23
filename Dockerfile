FROM python:3.12-alpine

ENV PYTHONUNBUFFERED=1
# Define where Chrome will store its user data
ENV CHROME_USER_DATA_DIR=/home/user/chrome_data

RUN apk add --no-cache chromium chromium-chromedriver

# Create symbolic links for the expected Google Chrome names to the Chromium executable
RUN ln -s /usr/bin/chromium-browser /usr/bin/google-chrome && \
    ln -s /usr/bin/chromium-browser /usr/bin/google-chrome-stable && \
    ln -s /usr/bin/chromium-browser /usr/bin/google-chrome-beta && \
    ln -s /usr/bin/chromium-browser /usr/bin/google-chrome-dev
    
# Copy the requirements file and install Python dependencies
COPY ./requirements.yaml /requirements.yaml
RUN pip install --upgrade pip && pip install -r /requirements.yaml

# Create a directory for your application and set permissions
RUN mkdir /app && chmod -R 777 /app

# Copy the application files
COPY . /app

# Set read/write permissions for the data directory
RUN chmod -R 777 /app/data

# Set the working directory
WORKDIR /app

# Use an unprivileged user to run the app
# RUN adduser -D user
# USER user

CMD ["python3", "allConfigsRunner.py"]
