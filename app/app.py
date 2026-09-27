from flask import Flask

app = Flask(__name__)


@app.route("/")
def home():
    return "Hello from my Azure App Service!"


@app.route("/health")
def health():
    return {
        "status": "healthy",
        "service": "Azure App Service"
    }


if __name__ == "__main__":
    app.run()