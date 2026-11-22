import axios from "axios";

const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1";

export const api = axios.create({
  baseURL: apiBaseUrl,
});

api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = window.sessionStorage.getItem("agentos_access_token");
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }

  return config;
});

let refreshPromise: Promise<string> | null = null;

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

    const refreshToken = window.sessionStorage.getItem("agentos_refresh_token");
    if (!refreshToken) {
      window.sessionStorage.removeItem("agentos_access_token");
      return Promise.reject(error);
    }

    try {
      refreshPromise ??= api
        .post("/auth/refresh", { refresh_token: refreshToken })
        .then((response) => {
          window.sessionStorage.setItem(
            "agentos_access_token",
            response.data.access_token,
          );
          window.sessionStorage.setItem(
            "agentos_refresh_token",
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
      window.sessionStorage.removeItem("agentos_access_token");
      window.sessionStorage.removeItem("agentos_refresh_token");
      return Promise.reject(error);
    }
  },
);
