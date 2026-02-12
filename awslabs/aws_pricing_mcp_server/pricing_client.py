# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""awslabs MCP AWS Pricing mcp server pricing client.

This module provides utilities for fetching pricing data from the
AWS Price List Bulk API (public endpoints, no authentication required).
"""

import httpx
import sys
from awslabs.aws_pricing_mcp_server import consts
from loguru import logger
from typing import Any, Dict, List, Optional


# Set up logging
logger.remove()
logger.add(sys.stderr, level=consts.LOG_LEVEL)

BASE_URL = consts.BULK_API_BASE_URL


async def fetch_service_index() -> Dict[str, Any]:
    """Fetch the service index listing all available AWS services.

    Returns:
        Parsed JSON with structure {"offers": {"ServiceCode": {"offerCode": ..., ...}, ...}}

    Raises:
        httpx.HTTPStatusError: If the HTTP request fails
    """
    url = f'{BASE_URL}/offers/v1.0/aws/index.json'
    logger.debug(f'Fetching service index from {url}')
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        return response.json()


async def fetch_price_list(
    service_code: str, region: Optional[str] = None
) -> Dict[str, Any]:
    """Fetch the price list for a service, optionally scoped to a region.

    Args:
        service_code: AWS service code (e.g., 'AmazonEC2', 'AWSLambda')
        region: Optional AWS region code (e.g., 'us-east-1'). If None, fetches the
                global price list (all regions).

    Returns:
        Parsed JSON with structure {"products": {...}, "terms": {...}, ...}

    Raises:
        httpx.HTTPStatusError: If the HTTP request fails
    """
    if region:
        url = f'{BASE_URL}/offers/v1.0/aws/{service_code}/current/{region}/index.json'
    else:
        url = f'{BASE_URL}/offers/v1.0/aws/{service_code}/current/index.json'
    logger.debug(f'Fetching price list from {url}')
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=120.0)
        response.raise_for_status()
        return response.json()


async def fetch_region_index(service_code: str) -> Dict[str, Any]:
    """Fetch the region index for a service (lists available regions).

    Args:
        service_code: AWS service code (e.g., 'AmazonEC2')

    Returns:
        Parsed JSON with region index data

    Raises:
        httpx.HTTPStatusError: If the HTTP request fails
    """
    url = f'{BASE_URL}/offers/v1.0/aws/{service_code}/current/region_index.json'
    logger.debug(f'Fetching region index from {url}')
    async with httpx.AsyncClient() as client:
        response = await client.get(url, timeout=30.0)
        response.raise_for_status()
        return response.json()


def _join_products_and_terms(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Join the separate products and terms sections into per-product records.

    The Bulk API returns:
        {"products": {"SKU": {...}}, "terms": {"OnDemand": {"SKU": {...}}, "Reserved": {"SKU": {...}}}}

    This function produces the per-product format the rest of the codebase expects:
        [{"product": {...}, "terms": {"OnDemand": {...}, "Reserved": {...}}}]

    Args:
        data: Raw parsed JSON from the Bulk API

    Returns:
        List of per-product records with joined terms
    """
    products = data.get('products', {})
    terms_section = data.get('terms', {})

    result = []
    for sku, product_data in products.items():
        product_terms = {}
        for term_type, term_skus in terms_section.items():
            if sku in term_skus:
                product_terms[term_type] = term_skus[sku]

        result.append({
            'product': product_data,
            'terms': product_terms,
        })

    return result


def _apply_filters(
    products: List[Dict[str, Any]], filters: List[Dict[str, str]]
) -> List[Dict[str, Any]]:
    """Apply filters locally to product records.

    Implements the same filter types that the AWS Pricing Query API supports:
    - EQUALS: Exact match
    - ANY_OF: Value matches any in a comma-separated list
    - CONTAINS: Value is a substring of the attribute
    - NONE_OF: Value does NOT match any in a comma-separated list

    Args:
        products: List of per-product records from _join_products_and_terms()
        filters: List of filter dicts with keys 'Field', 'Type', 'Value'

    Returns:
        Filtered list of product records
    """
    if not filters:
        return products

    filtered = []
    for product in products:
        attrs = product.get('product', {}).get('attributes', {})
        match = True

        for f in filters:
            field = f['Field']
            filter_type = f.get('Type', 'EQUALS')
            value = f['Value']
            attr_value = attrs.get(field, '')

            if filter_type == 'EQUALS':
                if attr_value != value:
                    match = False
                    break
            elif filter_type == 'ANY_OF':
                values = value.split(',') if isinstance(value, str) else value
                if attr_value not in values:
                    match = False
                    break
            elif filter_type == 'CONTAINS':
                if value not in attr_value:
                    match = False
                    break
            elif filter_type == 'NONE_OF':
                values = value.split(',') if isinstance(value, str) else value
                if attr_value in values:
                    match = False
                    break

        if match:
            filtered.append(product)

    return filtered


def get_pricing_region(requested_region: Optional[str] = None) -> str:
    """Determine the appropriate AWS Pricing API region.

    Maps the requested region to the nearest pricing-data region. This is still
    used for constructing Bulk API URLs with region-specific price lists.

    Args:
        requested_region: The AWS region requested by the user (default: None)

    Returns:
        The appropriate pricing region code
    """
    if not requested_region:
        requested_region = consts.AWS_REGION

    # Map regions based on prefix to nearest pricing endpoint
    if requested_region.startswith('cn-'):
        return 'cn-northwest-1'
    elif requested_region.startswith(('eu-', 'me-', 'af-')):
        return 'eu-central-1'
    elif requested_region.startswith('ap-'):
        return 'ap-south-1'
    elif requested_region.startswith('eusc-'):
        return 'eusc-de-east-1'
    else:
        return requested_region


def get_currency_for_region(region: str) -> str:
    """Determine currency based on AWS region.

    Args:
        region: AWS region code (e.g., 'us-east-1', 'cn-north-1')

    Returns:
        'CNY' for China partition regions (cn-*), 'USD' otherwise
    """
    return 'CNY' if region.startswith('cn-') else 'USD'
