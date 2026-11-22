import axios from "axios";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";
const accessTokenKey = "agentos_access_token";
const refreshTokenKey = "agentos_refresh_token";

export const api = axios.create({
  baseURL: apiBaseUrl,
});

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = window.sessionStorage.getItem(accessTokenKey);
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }

  return config;
});

let refreshPromise: Promise<string> | null = null;

function clearTokens(): void {
  window.sessionStorage.removeItem(accessTokenKey);
  window.sessionStorage.removeItem(refreshTokenKey);
}

function storeTokens(accessToken: string, refreshToken: string): void {
  window.sessionStorage.setItem(accessTokenKey, accessToken);
  window.sessionStorage.setItem(refreshTokenKey, refreshToken);
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (
      error.response?.status !== 401 ||
      originalRequest?.url?.includes("/auth/") ||
      originalRequest?._retry
    ) {
      return Promise.reject(error);
    }

    if (typeof window === "undefined") {
      return Promise.reject(error);
    }

    const refreshToken = window.sessionStorage.getItem(refreshTokenKey);
    if (!refreshToken) {
      clearTokens();
      return Promise.reject(error);
    }

    try {
      refreshPromise ??= api
        .post("/auth/refresh", { refresh_token: refreshToken })
        .then((response) => {
          storeTokens(
            response.data.access_token,
            response.data.refresh_token,
          );
          return response.data.access_token as string;
        })
        .finally(() => {
          refreshPromise = null;
        });

      const accessToken = await refreshPromise;
      originalRequest._retry = true;
      originalRequest.headers.Authorization = `Bearer ${accessToken}`;
      return api(originalRequest);
    } catch {
      clearTokens();
      return Promise.reject(error);
    }
  },
);
