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

"""Tests for the pricing client module."""

import pytest
from awslabs.aws_pricing_mcp_server.pricing_client import (
    _apply_filters,
    _join_products_and_terms,
    get_currency_for_region,
    get_pricing_region,
)


class TestGetPricingRegion:
    """Tests for the get_pricing_region function."""

    @pytest.mark.parametrize(
        'region,expected',
        [
            # Direct pricing regions (returned as-is since they are valid)
            ('us-east-1', 'us-east-1'),
            ('eu-central-1', 'eu-central-1'),
            ('ap-south-1', 'ap-south-1'),
            ('cn-northwest-1', 'cn-northwest-1'),
            # US/Americas regions (returned as-is since they are valid region codes)
            ('us-west-2', 'us-west-2'),
            ('ca-central-1', 'ca-central-1'),
            ('sa-east-1', 'sa-east-1'),
            # Europe/Middle East/Africa regions
            ('eu-west-1', 'eu-central-1'),
            ('me-south-1', 'eu-central-1'),
            ('af-south-1', 'eu-central-1'),
            # Asia Pacific regions
            ('ap-east-1', 'ap-south-1'),
            # European Sovereign Cloud regions
            ('eusc-de-east-1', 'eusc-de-east-1'),
            ('eusc-de-west-1', 'eusc-de-east-1'),
            # China regions
            ('cn-north-1', 'cn-northwest-1'),
            # Unknown regions default to themselves (valid region codes)
            ('unknown-region', 'unknown-region'),
        ],
    )
    def test_region_mapping(self, region, expected):
        """Test region mapping to pricing endpoints."""
        assert get_pricing_region(region) == expected

    @pytest.mark.parametrize(
        'env_region,expected',
        [
            ('eu-west-1', 'eu-central-1'),
            ('us-east-1', 'us-east-1'),
            ('ap-northeast-1', 'ap-south-1'),
        ],
    )
    def test_uses_aws_region_env_var(self, env_region, expected, monkeypatch):
        """Test AWS_REGION env var is used when no region specified."""
        monkeypatch.setattr('awslabs.aws_pricing_mcp_server.consts.AWS_REGION', env_region)
        assert get_pricing_region() == expected


class TestJoinProductsAndTerms:
    """Tests for the _join_products_and_terms function."""

    def test_basic_join(self):
        """Test basic joining of products and terms."""
        data = {
            'products': {
                'SKU1': {
                    'sku': 'SKU1',
                    'productFamily': 'Compute',
                    'attributes': {'instanceType': 'm5.large'},
                },
            },
            'terms': {
                'OnDemand': {
                    'SKU1': {
                        'SKU1.TERM1': {
                            'priceDimensions': {
                                'SKU1.TERM1.DIM1': {
                                    'unit': 'Hrs',
                                    'pricePerUnit': {'USD': '0.096'},
                                }
                            }
                        }
                    }
                }
            },
        }
        result = _join_products_and_terms(data)
        assert len(result) == 1
        assert result[0]['product']['sku'] == 'SKU1'
        assert 'OnDemand' in result[0]['terms']

    def test_multiple_products(self):
        """Test joining multiple products."""
        data = {
            'products': {
                'SKU1': {'sku': 'SKU1', 'attributes': {'type': 'a'}},
                'SKU2': {'sku': 'SKU2', 'attributes': {'type': 'b'}},
            },
            'terms': {
                'OnDemand': {
                    'SKU1': {'term1': {}},
                    'SKU2': {'term2': {}},
                },
                'Reserved': {
                    'SKU1': {'rterm1': {}},
                },
            },
        }
        result = _join_products_and_terms(data)
        assert len(result) == 2

        skus = {r['product']['sku'] for r in result}
        assert skus == {'SKU1', 'SKU2'}

        # SKU1 should have both OnDemand and Reserved
        sku1 = next(r for r in result if r['product']['sku'] == 'SKU1')
        assert 'OnDemand' in sku1['terms']
        assert 'Reserved' in sku1['terms']

        # SKU2 should have only OnDemand
        sku2 = next(r for r in result if r['product']['sku'] == 'SKU2')
        assert 'OnDemand' in sku2['terms']
        assert 'Reserved' not in sku2['terms']

    def test_empty_data(self):
        """Test with empty products and terms."""
        result = _join_products_and_terms({'products': {}, 'terms': {}})
        assert result == []

    def test_missing_terms(self):
        """Test with products but no matching terms."""
        data = {
            'products': {
                'SKU1': {'sku': 'SKU1', 'attributes': {}},
            },
            'terms': {'OnDemand': {}},
        }
        result = _join_products_and_terms(data)
        assert len(result) == 1
        assert result[0]['terms'] == {}


class TestApplyFilters:
    """Tests for the _apply_filters function."""

    def _make_product(self, **attrs):
        """Helper to create a product record."""
        return {'product': {'attributes': attrs}, 'terms': {}}

    def test_equals_filter(self):
        """Test EQUALS filter type."""
        products = [
            self._make_product(instanceType='t3.medium'),
            self._make_product(instanceType='m5.large'),
        ]
        filters = [{'Field': 'instanceType', 'Type': 'EQUALS', 'Value': 't3.medium'}]
        result = _apply_filters(products, filters)
        assert len(result) == 1
        assert result[0]['product']['attributes']['instanceType'] == 't3.medium'

    def test_any_of_filter(self):
        """Test ANY_OF filter type with comma-separated values."""
        products = [
            self._make_product(instanceType='t3.medium'),
            self._make_product(instanceType='m5.large'),
            self._make_product(instanceType='c5.xlarge'),
        ]
        filters = [{'Field': 'instanceType', 'Type': 'ANY_OF', 'Value': 't3.medium,m5.large'}]
        result = _apply_filters(products, filters)
        assert len(result) == 2
        types = {r['product']['attributes']['instanceType'] for r in result}
        assert types == {'t3.medium', 'm5.large'}

    def test_contains_filter(self):
        """Test CONTAINS filter type."""
        products = [
            self._make_product(instanceType='t3.medium'),
            self._make_product(instanceType='t3.large'),
            self._make_product(instanceType='m5.large'),
        ]
        filters = [{'Field': 'instanceType', 'Type': 'CONTAINS', 'Value': 't3'}]
        result = _apply_filters(products, filters)
        assert len(result) == 2
        for r in result:
            assert 't3' in r['product']['attributes']['instanceType']

    def test_none_of_filter(self):
        """Test NONE_OF filter type."""
        products = [
            self._make_product(instanceType='t2.micro'),
            self._make_product(instanceType='t3.medium'),
            self._make_product(instanceType='m5.large'),
        ]
        filters = [{'Field': 'instanceType', 'Type': 'NONE_OF', 'Value': 't2.micro,m5.large'}]
        result = _apply_filters(products, filters)
        assert len(result) == 1
        assert result[0]['product']['attributes']['instanceType'] == 't3.medium'

    def test_multiple_filters(self):
        """Test multiple filters applied together (AND logic)."""
        products = [
            self._make_product(instanceType='t3.medium', tenancy='Shared'),
            self._make_product(instanceType='t3.medium', tenancy='Dedicated'),
            self._make_product(instanceType='m5.large', tenancy='Shared'),
        ]
        filters = [
            {'Field': 'instanceType', 'Type': 'EQUALS', 'Value': 't3.medium'},
            {'Field': 'tenancy', 'Type': 'EQUALS', 'Value': 'Shared'},
        ]
        result = _apply_filters(products, filters)
        assert len(result) == 1
        assert result[0]['product']['attributes']['instanceType'] == 't3.medium'
        assert result[0]['product']['attributes']['tenancy'] == 'Shared'

    def test_empty_filters(self):
        """Test that empty filters return all products."""
        products = [self._make_product(instanceType='t3.medium')]
        assert _apply_filters(products, []) == products

    def test_no_matches(self):
        """Test that filters returning no matches return empty list."""
        products = [self._make_product(instanceType='t3.medium')]
        filters = [{'Field': 'instanceType', 'Type': 'EQUALS', 'Value': 'nonexistent'}]
        result = _apply_filters(products, filters)
        assert result == []

    def test_missing_attribute(self):
        """Test filter on attribute that doesn't exist in product."""
        products = [self._make_product(instanceType='t3.medium')]
        filters = [{'Field': 'nonexistent', 'Type': 'EQUALS', 'Value': 'foo'}]
        result = _apply_filters(products, filters)
        assert result == []


class TestGetCurrencyForRegion:
    """Tests for the get_currency_for_region function."""

    @pytest.mark.parametrize(
        'region,expected',
        [
            # China regions
            ('cn-north-1', 'CNY'),
            ('cn-northwest-1', 'CNY'),
            ('cn-south-1', 'CNY'),
            # Other regions
            ('us-east-1', 'USD'),
            ('us-west-2', 'USD'),
            ('eu-west-1', 'USD'),
            ('ap-southeast-1', 'USD'),
            ('unknown-region', 'USD'),
        ],
    )
    def test_currency_mapping(self, region, expected):
        """Test currency mapping for different regions."""
        assert get_currency_for_region(region) == expected
