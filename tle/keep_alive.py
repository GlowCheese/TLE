from flask import Flask
from os import environ
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "I'm alive"

def run():
    app.run(host='0.0.0.0',port=int(environ.get('PORT')))

def keep_alive():
    t = Thread(target=run)
    t.start()