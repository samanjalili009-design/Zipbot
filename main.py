k
    import threading
    
    web_app = Flask(__name__)
    
    @web_app.route('/health')
    def health_check():
        return "Bot is running", 200
    
    def run_flask():
        port = int(os.environ.get("PORT", 10000))
        web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    app.run()
