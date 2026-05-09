import sys

with open('docker-compose.yml', 'r') as f:
    lines = f.readlines()

new_lines = []
in_services = False
for i, line in enumerate(lines):
    new_lines.append(line)
    if line.strip() == 'services:':
        new_lines.append('''
  timescaledb:
    image: timescale/timescaledb:latest-pg15
    container_name: algovibe-timescaledb
    restart: always
    environment:
      - POSTGRES_USER=algovibe
      - POSTGRES_PASSWORD=secretpassword
    volumes:
      - algovibe_pgdata:/var/lib/postgresql/data
      - ./init-pg.sh:/docker-entrypoint-initdb.d/init-pg.sh
    ports:
      - "5432:5432"
    networks:
      - default
''')
    if line.strip() == 'depends_on:':
        # we are at a depends_on block, insert timescaledb dependency next
        # wait, we should check if it's using dictionary format or list format
        if i+1 < len(lines) and 'condition:' in lines[i+1]:
            new_lines.append('      timescaledb:\n        condition: service_started\n')
        else:
            new_lines.append('      - timescaledb\n')

for i, line in enumerate(new_lines):
    if line.strip() == 'volumes:':
        new_lines.insert(i+1, '  algovibe_pgdata:\n')
        break

with open('docker-compose.yml', 'w') as f:
    f.writelines(new_lines)
