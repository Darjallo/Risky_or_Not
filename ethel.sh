#!/bin/bash

# Check if exactly one argument is provided
if [ "$#" -lt 1 ]; then
    echo "Usage: $0 [up|down|ps|flush|log] {service_name}"
    exit 1
fi

# Perform action based on the argument
case $1 in
    up)
        echo "Deploying stack..."
        docker stack deploy -c docker-compose.yml ethelstack
        ;;
    down)
        echo "Removing stack..."
        docker stack rm ethelstack
        ;;
    ps)
        echo "Listing services in the stack..."
        docker stack ps ethelstack --no-trunc
        ;;
    flush)
        echo "Restarting everything..."
        systemctl restart docker
        ;;
    log)
	echo "Log for ethelstack_$2"
        docker service logs ethelstack_$2
	;;
    *)
        echo "Invalid argument: $1"
        echo "Usage: $0 [up|down|ps|flush]"
        exit 2
        ;;
esac
