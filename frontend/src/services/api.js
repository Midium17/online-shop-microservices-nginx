import axios from 'axios';

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || 'http://localhost:8080';

export const getToken = () => localStorage.getItem('token');

const API = axios.create({
  baseURL: API_BASE_URL,
});

// Automatically attach the JWT token to requests if available
API.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers = config.headers || {};
    if (!config.headers.Authorization) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }

  // Allow FormData (such as file/CSV uploads) to set its own multipart boundary
  if (config.data instanceof FormData && config.headers) {
    delete config.headers['Content-Type'];
  }

  return config;
});

export default API;