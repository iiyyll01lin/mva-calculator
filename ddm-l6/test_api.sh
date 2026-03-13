#!/bin/bash
BASE="http://localhost:8000/api/v1"

echo "=== Health Check ==="
curl -s $BASE/health | jq .

echo -e "\n=== Login (Engineer) ==="
RESP=$(curl -s -X POST $BASE/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"engineer1","password":"eng123"}')
echo $RESP | jq .
TOKEN=$(echo $RESP | jq -r .access_token)

echo -e "\n=== Master Data: Objects ==="
curl -s -H "Authorization: Bearer $TOKEN" $BASE/master/objects | jq '. | length'

echo -e "\n=== Master Data: Glove Rules ==="
curl -s -H "Authorization: Bearer $TOKEN" $BASE/master/glove-rules | jq '. | length'

echo -e "\n=== MOST Calculate ==="
curl -s -X POST $BASE/most/calculate \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"steps":[{"action":"拿取","object":"DIMM","seq_type":"GENERAL"}]}' | jq .

echo -e "\n=== SOP Versions ==="
curl -s -H "Authorization: Bearer $TOKEN" $BASE/sop/versions | jq '.[0].id'

echo -e "\n=== Line Balance Simulation ==="
curl -s -X POST $BASE/simulation/line-balance \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"project_id":"proj-001","stations":[{"id":"ST-3-1a","sop_ids":[],"employee_id":"emp-001"}],"takt_time":30}' | jq '{bottleneck:.bottleneck_station,balance_rate:.balance_rate}'

echo -e "\n=== All Tests Complete ==="
