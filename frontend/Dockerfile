FROM nginx:1.27-alpine

COPY frontend/site/ /usr/share/nginx/html/
COPY frontend/nginx/default.conf.template /etc/nginx/templates/default.conf.template

# In the container the console and trust page read the API through the nginx proxy.
RUN sed -i 's#source: "sample"#source: "live"#; s#apiBase: ""#apiBase: "/api"#' /usr/share/nginx/html/scripts/config.js \
 && grep -q 'source: "live"' /usr/share/nginx/html/scripts/config.js

# Only GHAST_* variables are substituted into the template, so nginx's own $variables survive.
ENV NGINX_ENVSUBST_FILTER=GHAST_

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
