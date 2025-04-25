// frontend/lib/websocket.ts

// Make the listener type generic
type MessageListener<T> = (data: T) => void;

class WebSocketClient {
  private socket: WebSocket | null = null;
  private url: string;
  private pingInterval: number = 30000; // Interval in milliseconds (30 seconds)
  private pingTimer: NodeJS.Timeout | undefined;
  // Use MessageListener<unknown> for the internal map
  private listeners: Map<string, Set<MessageListener<unknown>>> = new Map();

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

  // Make subscribe generic to accept typed listeners
  public subscribe<T>(messageType: string, listener: MessageListener<T>): void {
    if (!this.listeners.has(messageType)) {
      this.listeners.set(messageType, new Set());
    }
    // Cast the specific listener to MessageListener<unknown> for storage
    this.listeners.get(messageType)?.add(listener as MessageListener<unknown>);
  }

  // Make unsubscribe generic
  public unsubscribe<T>(messageType: string, listener: MessageListener<T>): void {
    // Cast the specific listener to MessageListener<unknown> for lookup/deletion
    this.listeners.get(messageType)?.delete(listener as MessageListener<unknown>);
    if (this.listeners.get(messageType)?.size === 0) {
      this.listeners.delete(messageType);
    }
  }

  private handleIncomingMessage(message: string): void {
    try {
      const parsedData = JSON.parse(message);
      const messageType = parsedData.type;
      // Explicitly type messagePayload as unknown
      const messagePayload: unknown = parsedData.data;

      if (messageType === 'ping') {
        console.log('Received ping, sending pong');
        this.sendMessage('pong', {}); // Respond with a pong
      } else {
        console.log(`Received message type: ${messageType}`, messagePayload);
        // Notify listeners for this message type
        if (this.listeners.has(messageType)) {
          // Iterate over listeners typed as MessageListener<unknown>
          this.listeners.get(messageType)?.forEach((listener: MessageListener<unknown>) => {
            try {
              // Call the listener with the unknown payload.
              // Type safety relies on the consumer subscribing with the correct type T
              // and the listener function expecting that type T.
              listener(messagePayload);
            } catch (error) {
              console.error(`Error in listener for message type ${messageType}:`, error);
            }
          });
        }
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