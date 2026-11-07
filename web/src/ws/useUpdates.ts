import { useEffect, useRef, useState } from 'react';
import { wsUrl } from '../api/client';
import type { WsEvent } from '../api/types';
import { useAuth } from '../state/AuthContext';

/** Подписка на live-события сервера с авто-reconnect. Возвращает статус соединения. */
export function useUpdates(onEvent: (event: WsEvent) => void): boolean {
  const { token } = useAuth();
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!token) return;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    const connect = () => {
      socket = new WebSocket(wsUrl());
      socket.onopen = () => setConnected(true);
      socket.onmessage = (e) => {
        try {
          handler.current(JSON.parse(e.data as string) as WsEvent);
        } catch {
          // некорректное сообщение — игнорируем
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!disposed) retry = setTimeout(connect, 2000);
      };
    };
    connect();

    return () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [token]);

  return connected;
}
