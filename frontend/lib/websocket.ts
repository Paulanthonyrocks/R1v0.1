// frontend/lib/websocket.ts

class WebSocketClient {
  private socket: WebSocket | null = null;
  private url: string;
  private pingInterval: number = 30000; // Interval in milliseconds (30 seconds)
  private pingTimer: NodeJS.Timeout | undefined;

  constructor(url: string) {
    this.url = url;
  }

  public connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.socket = new WebSocket(this.url);

      this.socket.onopen = () => {
        console.log('WebSocket connected');
        this.startPinging();
        resolve();
      };

      this.socket.onclose = (event) => {
        if (event.wasClean) {
          console.log(`WebSocket closed cleanly, code=${event.code}, reason=${event.reason}`);
        } else {
          console.error('WebSocket connection died');
        }
        this.socket = null;
      };

      this.socket.onerror = (error) => {
        clearInterval(this.pingTimer);
        console.error('WebSocket error:', error);
        reject(error);
      };

      this.socket.onmessage = (event) => {
        this.handleIncomingMessage(event.data);
      };
    });
  }

  public disconnect(): void {
    clearInterval(this.pingTimer);
    if (this.socket) {
      this.socket.close();
    }
  }

  public sendMessage(type: string, data: unknown): void {
    const message = { type, data };
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
    } else {
      console.error('WebSocket is not open. Cannot send message:', message);
    }
  }
  
  private handleIncomingMessage(message: string): void {
    try {
      const data = JSON.parse(message);
      if (data.type === 'ping') {
        console.log('Received ping, sending pong');
        this.sendMessage('pong', {}); // Respond with a pong
      } else {
        console.log('Received message:', data);
      }
    } catch (error) {
      console.error('Error parsing incoming message:', error);
    }
  }

  private startPinging(): void {
    this.pingTimer = setInterval(() => {
      this.sendMessage('ping', {});
      console.log('Sent ping');
    }, this.pingInterval);
  }

}

const ws = new WebSocketClient('ws://localhost:8000/ws');
export { ws, WebSocketClient as default };