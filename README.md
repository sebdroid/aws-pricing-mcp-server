# AWS Pricing MCP Server (Fork)

An MCP server for accessing real-time AWS pricing information and providing cost analysis capabilities. Forked from [awslabs/mcp](https://github.com/awslabs/mcp).

**Important Note**: This server provides real-time pricing data from the public AWS Price List Bulk API. We cannot guarantee that AI assistants will always construct filters correctly or identify the absolute cheapest options. All API calls are free of charge and **require no AWS credentials**.

## Changes from Upstream

This fork replaces the AWS Pricing Query API (`boto3`) with the **public AWS Price List Bulk API**, which means:

- **No AWS credentials required** — no IAM permissions, no `aws configure`, no `AWS_PROFILE` needed
- **No `boto3` dependency** — uses `httpx` for direct HTTP requests to the public Bulk API
- **Local filtering** — filters are applied locally after fetching price lists, supporting `EQUALS`, `ANY_OF`, `CONTAINS`, and `NONE_OF` filter types
- **Index-based pagination** — uses simple integer offsets instead of AWS API pagination tokens

## Features

### AWS Pricing Discovery & Information

- **Service catalog exploration**: Discover all AWS services with available pricing information
- **Pricing attribute discovery**: Identify filterable dimensions (instance types, regions, storage classes, etc.) for any AWS service
- **Real-time pricing queries**: Access current pricing data with advanced filtering capabilities including multi-option comparisons and pattern matching
- **Multi-region pricing comparisons**: Compare pricing across different AWS regions in a single query
- **Bulk pricing data access**: Download complete pricing datasets in CSV/JSON formats for historical analysis and offline processing

### Cost Analysis & Planning

- **Detailed cost report generation**: Create comprehensive cost analysis reports with unit pricing, calculation breakdowns, and usage scenarios
- **Infrastructure project analysis**: Scan CDK and Terraform projects to automatically identify AWS services and their configurations
- **Architecture pattern guidance**: Get detailed architecture patterns and cost considerations, especially for Amazon Bedrock services
- **Cost optimization recommendations**: Receive AWS Well-Architected Framework aligned suggestions for cost optimization

### Query pricing data with natural language

- Ask questions about AWS pricing in plain English, no complex query languages required
- Get instant answers from the AWS Price List Bulk API for any AWS service
- Retrieve comprehensive pricing information with flexible filtering options

## Prerequisites

1. Install `uv` from [Astral](https://docs.astral.sh/uv/getting-started/installation/) or the [GitHub README](https://github.com/astral-sh/uv#installation)
2. Install Python using `uv python install 3.10`

No AWS credentials or IAM permissions are needed — this fork uses the public Bulk API.

## Installation

### From GitHub (recommended)

Configure the MCP server in your MCP client configuration:

**For Linux/MacOS:**

```json
{
  "mcpServers": {
    "aws-pricing": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/sebdroid/aws-pricing-mcp-server",
        "awslabs.aws-pricing-mcp-server"
      ],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

**For Windows:**

```json
{
  "mcpServers": {
    "aws-pricing": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/sebdroid/aws-pricing-mcp-server",
        "awslabs.aws-pricing-mcp-server.exe"
      ],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

### From a local clone

```bash
git clone https://github.com/sebdroid/aws-pricing-mcp-server.git
cd aws-pricing-mcp-server
uv sync
```

Then configure your MCP client to use the local directory:

```json
{
  "mcpServers": {
    "aws-pricing": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/aws-pricing-mcp-server",
        "awslabs.aws-pricing-mcp-server"
      ],
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR"
      }
    }
  }
}
```

### Using Docker

After a successful `docker build -t awslabs/aws-pricing-mcp-server .`:

```json
{
  "mcpServers": {
    "aws-pricing": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "--interactive",
        "--env",
        "FASTMCP_LOG_LEVEL=ERROR",
        "awslabs/aws-pricing-mcp-server:latest"
      ]
    }
  }
}
```

## Configuration

The server uses one optional environment variable:

- **`AWS_REGION`**: Sets the default region for pricing queries when no region is specified (default: `us-east-1`). This does **not** require AWS credentials — it simply controls which regional price list is fetched from the public Bulk API.

```json
"env": {
  "FASTMCP_LOG_LEVEL": "ERROR",
  "AWS_REGION": "us-east-1"
}
```
