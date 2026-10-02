FROM nginx:1.27-alpine

COPY frontend/site/ /usr/share/nginx/html/
COPY frontend/nginx/default.conf.template /etc/nginx/templates/default.conf.template

# Only GHAST_* variables are substituted into the template, so nginx's own $variables survive.
ENV NGINX_ENVSUBST_FILTER=GHAST_

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
