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

"""Tests for the server module of the aws-pricing-mcp-server."""

import pytest
from awslabs.aws_pricing_mcp_server.models import PricingFilter
from awslabs.aws_pricing_mcp_server.pricing_transformer import (
    _is_free_product,
)
from awslabs.aws_pricing_mcp_server.server import (
    analyze_cdk_project_wrapper,
    generate_cost_report_wrapper,
    get_bedrock_patterns,
    get_price_list_urls,
    get_pricing,
    get_pricing_attribute_values,
    get_pricing_service_attributes,
    get_pricing_service_codes,
)
from unittest.mock import AsyncMock, patch


def _make_bulk_response(products_list):
    """Create a Bulk API response from a list of product dicts.

    Each product dict should have 'sku' and optionally 'attributes', 'productFamily',
    'pricePerUnit', 'unit' keys.
    """
    products = {}
    terms = {'OnDemand': {}}
    for i, p in enumerate(products_list):
        sku = p.get('sku', f'SKU{i:03d}')
        products[sku] = {
            'sku': sku,
            'productFamily': p.get('productFamily', 'Compute'),
            'attributes': p.get('attributes', {}),
        }
        terms['OnDemand'][sku] = {
            f'{sku}.JRTCKXETXF': {
                'priceDimensions': {
                    f'{sku}.JRTCKXETXF.6YS6EN2CT7': {
                        'unit': p.get('unit', 'Hrs'),
                        'pricePerUnit': p.get('pricePerUnit', {'USD': '0.10'}),
                        'description': p.get('description', ''),
                    }
                }
            }
        }
    return {'products': products, 'terms': terms}


class TestAnalyzeCdkProject:
    """Tests for the analyze_cdk_project_wrapper function."""

    @pytest.mark.asyncio
    async def test_analyze_valid_project(self, mock_context, sample_cdk_project):
        """Test analyzing a valid CDK project."""
        result = await analyze_cdk_project_wrapper(mock_context, sample_cdk_project)

        assert result is not None
        assert result['status'] == 'success'
        assert 'services' in result

        services = {service['name'] for service in result['services']}
        assert 'lambda' in services
        assert 'dynamodb' in services
        assert 's3' in services
        assert 'iam' in services

    @pytest.mark.asyncio
    async def test_analyze_invalid_project(self, mock_context, temp_output_dir):
        """Test analyzing an invalid/empty project directory."""
        result = await analyze_cdk_project_wrapper(mock_context, temp_output_dir)

        assert result is not None
        assert result['status'] == 'success'
        assert 'services' in result
        assert len(result['services']) == 0

    @pytest.mark.asyncio
    async def test_analyze_nonexistent_project(self, mock_context):
        """Test analyzing a nonexistent project directory."""
        result = await analyze_cdk_project_wrapper(mock_context, '/nonexistent/path')

        assert result is not None
        assert 'services' in result
        assert len(result['services']) == 0


class TestGetPricing:
    """Tests for the get_pricing function."""

    @pytest.mark.asyncio
    async def test_get_valid_pricing(self, mock_context):
        """Test getting pricing for a valid service."""
        bulk_data = _make_bulk_response([
            {
                'sku': 'SKU001',
                'productFamily': 'Serverless',
                'attributes': {
                    'productFamily': 'Serverless',
                    'description': 'Run code without thinking about servers',
                },
                'pricePerUnit': {'USD': '0.20'},
                'unit': 'requests',
            }
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(mock_context, 'AWSLambda', 'us-west-2')

        assert result is not None
        assert result['status'] == 'success'
        assert result['service_name'] == 'AWSLambda'
        assert 'data' in result
        assert isinstance(result['data'], list)
        assert len(result['data']) > 0
        assert 'message' in result
        assert 'AWSLambda' in result['message']

    @pytest.mark.asyncio
    async def test_get_pricing_with_filters(self, mock_context):
        """Test getting pricing with filters."""
        bulk_data = _make_bulk_response([
            {
                'sku': 'SKU001',
                'attributes': {
                    'instanceType': 't3.medium',
                    'location': 'US East (N. Virginia)',
                },
            },
            {
                'sku': 'SKU002',
                'attributes': {
                    'instanceType': 'm5.large',
                    'location': 'US East (N. Virginia)',
                },
            },
        ])
        filters = [
            PricingFilter(Field='instanceType', Value='t3.medium'),
        ]

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(mock_context, 'AmazonEC2', 'us-east-1', filters)

        assert result is not None
        assert result['status'] == 'success'
        assert result['service_name'] == 'AmazonEC2'
        assert isinstance(result['data'], list)
        assert len(result['data']) == 1

    @pytest.mark.asyncio
    async def test_pricing_filter_model_validation(self):
        """Test that PricingFilter model validates correctly."""
        valid_filter = PricingFilter(Field='instanceType', Value='t3.medium')
        assert valid_filter.field == 'instanceType'
        assert valid_filter.value == 't3.medium'
        assert valid_filter.type == 'EQUALS'

        filter_dict = valid_filter.model_dump(by_alias=True)
        assert 'Field' in filter_dict
        assert 'Value' in filter_dict
        assert 'Type' in filter_dict
        assert filter_dict['Field'] == 'instanceType'
        assert filter_dict['Value'] == 't3.medium'
        assert filter_dict['Type'] == 'EQUALS'

    @pytest.mark.asyncio
    async def test_new_filter_types_validation(self):
        """Test that new filter types work correctly."""
        any_of_filter = PricingFilter(
            Field='instanceType', Value=['t3.medium', 'm5.large'], Type='ANY_OF'
        )
        assert any_of_filter.type == 'ANY_OF'
        assert any_of_filter.value == ['t3.medium', 'm5.large']

        contains_filter = PricingFilter(Field='instanceType', Value='m5', Type='CONTAINS')
        assert contains_filter.type == 'CONTAINS'
        assert contains_filter.value == 'm5'

        none_of_filter = PricingFilter(Field='instanceType', Value=['t2', 'm4'], Type='NONE_OF')
        assert none_of_filter.type == 'NONE_OF'
        assert none_of_filter.value == ['t2', 'm4']

        any_of_dict = any_of_filter.model_dump(by_alias=True)
        assert any_of_dict['Type'] == 'ANY_OF'
        assert any_of_dict['Value'] == 't3.medium,m5.large'

        contains_dict = contains_filter.model_dump(by_alias=True)
        assert contains_dict['Type'] == 'CONTAINS'
        assert contains_dict['Value'] == 'm5'

        none_of_dict = none_of_filter.model_dump(by_alias=True)
        assert none_of_dict['Type'] == 'NONE_OF'
        assert none_of_dict['Value'] == 't2,m4'

    @pytest.mark.asyncio
    async def test_filter_serialization_comma_separated(self):
        """Test that ANY_OF and NONE_OF filters serialize values as comma-separated strings."""
        any_of_filter = PricingFilter(
            Field='instanceType', Value=['t3.medium', 'm5.large'], Type='ANY_OF'
        )
        serialized = any_of_filter.model_dump(by_alias=True)
        assert serialized['Value'] == 't3.medium,m5.large'
        assert serialized['Type'] == 'ANY_OF'

        none_of_filter = PricingFilter(
            Field='instanceType', Value=['t2.micro', 'm4.large'], Type='NONE_OF'
        )
        serialized = none_of_filter.model_dump(by_alias=True)
        assert serialized['Value'] == 't2.micro,m4.large'
        assert serialized['Type'] == 'NONE_OF'

        equals_filter = PricingFilter(Field='instanceType', Value='m5.large', Type='EQUALS')
        serialized = equals_filter.model_dump(by_alias=True)
        assert serialized['Value'] == 'm5.large'
        assert serialized['Type'] == 'EQUALS'

        contains_filter = PricingFilter(Field='instanceType', Value='m5', Type='CONTAINS')
        serialized = contains_filter.model_dump(by_alias=True)
        assert serialized['Value'] == 'm5'
        assert serialized['Type'] == 'CONTAINS'

    @pytest.mark.asyncio
    async def test_multi_region_pricing(self, mock_context):
        """Test getting pricing for multiple regions."""
        bulk_data_r1 = _make_bulk_response([
            {'sku': 'SKU001', 'attributes': {'location': 'US East'}},
        ])
        bulk_data_r2 = _make_bulk_response([
            {'sku': 'SKU002', 'attributes': {'location': 'US West'}},
        ])
        bulk_data_r3 = _make_bulk_response([
            {'sku': 'SKU003', 'attributes': {'location': 'EU'}},
        ])

        async def mock_fetch(service_code, region=None):
            if region == 'us-east-1':
                return bulk_data_r1
            elif region == 'us-west-2':
                return bulk_data_r2
            elif region == 'eu-west-1':
                return bulk_data_r3
            return _make_bulk_response([])

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            side_effect=mock_fetch,
        ):
            result = await get_pricing(
                mock_context, 'AmazonEC2', ['us-east-1', 'us-west-2', 'eu-west-1']
            )

        assert result is not None
        assert result['status'] == 'success'
        assert result['service_name'] == 'AmazonEC2'
        assert len(result['data']) == 3

    @pytest.mark.asyncio
    async def test_single_region_backward_compatibility(self, mock_context):
        """Test that single region strings still work."""
        bulk_data = _make_bulk_response([
            {'sku': 'SKU001', 'attributes': {'instanceType': 'm5.large'}},
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ) as mock_fetch:
            result = await get_pricing(mock_context, 'AmazonEC2', 'us-east-1')

        assert result is not None
        assert result['status'] == 'success'
        mock_fetch.assert_called_once_with('AmazonEC2', 'us-east-1')

    @pytest.mark.asyncio
    async def test_get_pricing_response_structure_validation(self, mock_context):
        """Test that the response structure is properly validated."""
        bulk_data = _make_bulk_response([
            {
                'sku': 'ABC123',
                'productFamily': 'Compute',
                'attributes': {'instanceType': 't3.medium'},
                'pricePerUnit': {'USD': '0.0416'},
            }
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(mock_context, 'AmazonEC2', 'us-east-1')

        assert result['status'] == 'success'
        assert result['service_name'] == 'AmazonEC2'
        assert isinstance(result['data'], list)
        assert len(result['data']) == 1
        assert isinstance(result['message'], str)

        pricing_item = result['data'][0]
        assert 'product' in pricing_item
        assert 'terms' in pricing_item
        assert 'attributes' in pricing_item['product']
        assert 'OnDemand' in pricing_item['terms']

    @pytest.mark.asyncio
    async def test_get_pricing_empty_results(self, mock_context):
        """Test handling of empty pricing results."""
        empty_data = {'products': {}, 'terms': {}}
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=empty_data,
        ):
            result = await get_pricing(mock_context, 'InvalidService', 'us-west-2')

        assert result is not None
        assert result['status'] == 'error'
        assert result['error_type'] == 'empty_results'
        assert 'InvalidService' in result['message']
        assert 'No results found for given filters' in result['message']
        assert result['service_code'] == 'InvalidService'
        assert result['region'] == 'us-west-2'
        assert 'examples' in result
        assert 'suggestion' in result
        mock_context.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pricing_api_error(self, mock_context):
        """Test handling of API errors."""
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            side_effect=Exception('API Error'),
        ):
            result = await get_pricing(mock_context, 'AWSLambda', 'us-west-2')

        assert result is not None
        assert result['status'] == 'error'
        assert result['error_type'] == 'api_error'
        assert 'API Error' in result['message']
        assert result['service_code'] == 'AWSLambda'
        assert result['region'] == 'us-west-2'
        assert 'suggestion' in result
        mock_context.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pricing_data_processing_error(self, mock_context):
        """Test handling of data processing errors in transform_pricing_data."""
        # Create data that will cause transform_pricing_data to fail
        # by making _join_products_and_terms return items that serialize to invalid JSON
        # Actually, this is hard to trigger now since we control serialization.
        # Instead, mock transform_pricing_data to raise ValueError.
        bulk_data = _make_bulk_response([
            {'sku': 'SKU001', 'attributes': {'test': 'value'}},
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ), patch(
            'awslabs.aws_pricing_mcp_server.server.transform_pricing_data',
            side_effect=ValueError('Invalid JSON format'),
        ):
            result = await get_pricing(mock_context, 'AWSLambda', 'us-west-2')

        assert result is not None
        assert result['status'] == 'error'
        assert result['error_type'] == 'data_processing_error'
        assert 'Failed to process pricing data' in result['message']
        mock_context.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pricing_fetch_error(self, mock_context):
        """Test handling of fetch errors (replaces client creation error)."""
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            side_effect=Exception('Connection failed'),
        ):
            result = await get_pricing(mock_context, 'AWSLambda', 'us-west-2')

        assert result is not None
        assert result['status'] == 'error'
        assert result['error_type'] == 'api_error'
        assert 'Connection failed' in result['message']
        assert result['service_code'] == 'AWSLambda'
        assert result['region'] == 'us-west-2'
        mock_context.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pricing_result_threshold_exceeded(self, mock_context):
        """Test that the tool returns an error when result character count exceeds the threshold."""
        products = []
        for i in range(100):
            products.append({
                'sku': f'SKU{i:03d}',
                'productFamily': 'Compute Instance',
                'attributes': {
                    'instanceType': 'm5.large',
                    'location': 'US East (N. Virginia)',
                    'tenancy': 'Shared',
                    'operatingSystem': 'Linux',
                },
                'pricePerUnit': {'USD': '0.096'},
            })
        bulk_data = _make_bulk_response(products)

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(
                mock_context, 'AmazonEC2', 'us-east-1', max_allowed_characters=1000
            )

        assert result['status'] == 'error'
        assert result['error_type'] == 'result_too_large'
        assert 'exceeding the limit of 1,000' in result['message']
        assert 'output_options={"pricing_terms": ["OnDemand", "FlatRate"]}' in result['message']
        assert 'significantly reduce response size' in result['suggestion']
        assert len(result['sample_records']) == 3
        assert 'Add more specific filters' in result['suggestion']
        mock_context.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pricing_unlimited_results(self, mock_context):
        """Test that max_allowed_characters=-1 allows unlimited results."""
        products = []
        for i in range(100):
            products.append({
                'sku': f'SKU{i:03d}',
                'productFamily': 'Compute Instance',
                'attributes': {
                    'instanceType': 'm5.large',
                    'location': 'US East (N. Virginia)',
                },
                'pricePerUnit': {'USD': '0.096'},
            })
        bulk_data = _make_bulk_response(products)

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(
                mock_context, 'AmazonEC2', 'us-east-1', max_allowed_characters=-1
            )

        assert result['status'] == 'success'
        assert len(result['data']) == 100
        assert 'Retrieved pricing for AmazonEC2' in result['message']
        mock_context.info.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pricing_without_region(self, mock_context):
        """Test get_pricing works without region parameter for global services."""
        bulk_data = _make_bulk_response([
            {
                'sku': 'ABC123',
                'productFamily': 'Data Transfer',
                'attributes': {'productFamily': 'Data Transfer'},
            }
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ) as mock_fetch:
            result = await get_pricing(mock_context, 'AWSDataTransfer', region=None)

        assert result['status'] == 'success'
        assert result['service_name'] == 'AWSDataTransfer'
        # Should be called with no region (global)
        mock_fetch.assert_called_once_with('AWSDataTransfer')

    @pytest.mark.asyncio
    async def test_get_pricing_region_none_explicit(self, mock_context):
        """Test get_pricing with explicit region=None."""
        bulk_data = _make_bulk_response([
            {
                'sku': 'DEF456',
                'productFamily': 'CloudFront',
                'attributes': {'productFamily': 'CloudFront'},
            }
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ) as mock_fetch:
            result = await get_pricing(mock_context, 'AmazonCloudFront', None)

        assert result['status'] == 'success'
        assert result['service_name'] == 'AmazonCloudFront'
        mock_fetch.assert_called_once_with('AmazonCloudFront')

    @pytest.mark.asyncio
    async def test_get_pricing_with_filters_no_region(self, mock_context):
        """Test get_pricing with filters but no region."""
        filters = [PricingFilter(Field='operation', Value='DataTransfer-Out-Bytes')]

        bulk_data = _make_bulk_response([
            {
                'sku': 'GHI789',
                'productFamily': 'Data Transfer',
                'attributes': {'operation': 'DataTransfer-Out-Bytes'},
            },
            {
                'sku': 'JKL012',
                'productFamily': 'Data Transfer',
                'attributes': {'operation': 'DataTransfer-In-Bytes'},
            },
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(mock_context, 'AWSDataTransfer', None, filters)

        assert result['status'] == 'success'
        # Only one product should match the filter
        assert len(result['data']) == 1

    @pytest.mark.asyncio
    async def test_get_pricing_custom_threshold(self, mock_context):
        """Test that custom max_allowed_characters threshold works correctly."""
        small_products = [
            {'sku': f'SKU{i}', 'attributes': {'type': 'small'}}
            for i in range(10)
        ]
        bulk_data = _make_bulk_response(small_products)

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            # Should succeed with generous threshold
            result = await get_pricing(
                mock_context, 'AmazonEC2', 'us-east-1', None, max_allowed_characters=100000
            )
            assert result['status'] == 'success'
            assert len(result['data']) == 10

            # Should fail with tiny threshold
            result = await get_pricing(
                mock_context, 'AmazonEC2', 'us-east-1', None, max_allowed_characters=10
            )
            assert result['status'] == 'error'
            assert result['error_type'] == 'result_too_large'

    @pytest.mark.asyncio
    async def test_get_pricing_pagination_parameters(self, mock_context):
        """Test various pagination parameter combinations."""
        products = [
            {'sku': f'SKU{i:03d}', 'attributes': {'idx': str(i)}}
            for i in range(200)
        ]
        bulk_data = _make_bulk_response(products)

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            # Default: max_results=100, no next_token
            result = await get_pricing(mock_context, 'AmazonEC2', 'us-east-1')
            assert result['status'] == 'success'
            assert len(result['data']) == 100
            assert 'next_token' in result
            assert result['next_token'] == '100'

            # Custom max_results=25
            result = await get_pricing(
                mock_context, 'AmazonEC2', 'us-east-1', max_results=25
            )
            assert result['status'] == 'success'
            assert len(result['data']) == 25
            assert result['next_token'] == '25'

            # With next_token for page 2
            result = await get_pricing(
                mock_context, 'AmazonEC2', 'us-east-1', max_results=25, next_token='25'
            )
            assert result['status'] == 'success'
            assert len(result['data']) == 25
            assert result['next_token'] == '50'

    @pytest.mark.asyncio
    async def test_get_pricing_response_next_token(self, mock_context):
        """Test next_token handling in response."""
        # 150 products with max_results=100 should produce a next_token
        products = [
            {'sku': f'SKU{i:03d}', 'attributes': {'idx': str(i)}}
            for i in range(150)
        ]
        bulk_data = _make_bulk_response(products)

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(mock_context, 'AmazonEC2', 'us-east-1')
            assert result['status'] == 'success'
            assert 'next_token' in result
            assert result['next_token'] == '100'

        # 50 products with max_results=100 should NOT produce a next_token
        small_products = [
            {'sku': f'SKU{i:03d}', 'attributes': {'idx': str(i)}}
            for i in range(50)
        ]
        small_bulk = _make_bulk_response(small_products)

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=small_bulk,
        ):
            result = await get_pricing(mock_context, 'AmazonEC2', 'us-east-1')
            assert result['status'] == 'success'
            assert 'next_token' not in result

    @pytest.mark.asyncio
    async def test_get_pricing_with_alternatives(self, mock_context):
        """Test getting pricing for service with alternatives returns alternatives field."""
        bulk_data = _make_bulk_response([
            {
                'sku': 'SKU001',
                'productFamily': 'CloudFront',
                'attributes': {'productFamily': 'CloudFront'},
            }
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(mock_context, 'AmazonCloudFront', 'us-east-1')

        assert result is not None
        assert result['status'] == 'success'
        assert result['service_name'] == 'AmazonCloudFront'
        assert 'alternatives' in result
        assert isinstance(result['alternatives'], list)
        assert len(result['alternatives']) > 0

        alternative = result['alternatives'][0]
        assert alternative['service_code'] == 'CloudFrontPlans'
        assert 'keywords' in alternative
        assert 'bundled_services' in alternative
        assert 'description' in alternative

        assert 'alternatives' in result['message']
        assert 'CloudFrontPlans' in result['message']

    @pytest.mark.asyncio
    async def test_get_pricing_without_alternatives(self, mock_context):
        """Test getting pricing for service without alternatives has no alternatives field."""
        bulk_data = _make_bulk_response([
            {'sku': 'SKU001', 'attributes': {'instanceType': 'm5.large'}},
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(mock_context, 'AmazonEC2', 'us-east-1')

        assert result is not None
        assert result['status'] == 'success'
        assert result['service_name'] == 'AmazonEC2'
        assert 'alternatives' not in result
        assert 'alternatives' not in result['message']

    @pytest.mark.asyncio
    async def test_get_pricing_global_service_message(self, mock_context):
        """Test message format for global services without region."""
        bulk_data = _make_bulk_response([
            {'sku': 'SKU001', 'attributes': {'productFamily': 'Data Transfer'}},
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing(mock_context, 'AWSDataTransfer', None)

        assert result is not None
        assert result['status'] == 'success'
        assert 'globally' in result['message']
        assert 'in None' not in result['message']


class TestGetBedrockPatterns:
    """Tests for the get_bedrock_patterns function."""

    @pytest.mark.asyncio
    async def test_get_patterns(self, mock_context):
        """Test getting Bedrock architecture patterns."""
        result = await get_bedrock_patterns(mock_context)

        assert result is not None
        assert isinstance(result, str)
        assert 'Bedrock' in result
        assert 'Knowledge Base' in result


class TestGenerateCostReport:
    """Tests for the generate_cost_report_wrapper function."""

    @pytest.mark.asyncio
    async def test_generate_markdown_report(self, mock_context, sample_pricing_data_web):
        """Test generating a markdown cost report."""
        result = await generate_cost_report_wrapper(
            mock_context,
            pricing_data=sample_pricing_data_web,
            service_name='AWS Lambda',
            related_services=['DynamoDB'],
            pricing_model='ON DEMAND',
            assumptions=['Standard configuration'],
            exclusions=['Custom configurations'],
            format='markdown',
        )

        assert result is not None
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_generate_csv_report(self, mock_context, sample_pricing_data_web):
        """Test generating a CSV cost report."""
        result = await generate_cost_report_wrapper(
            mock_context,
            pricing_data=sample_pricing_data_web,
            service_name='AWS Lambda',
            format='csv',
            pricing_model='ON DEMAND',
            related_services=None,
            assumptions=None,
            exclusions=None,
            output_file=None,
            detailed_cost_data=None,
            recommendations=None,
        )

        assert result is not None
        assert isinstance(result, str)
        assert ',' in result

        lines = result.split('\n')
        assert len(lines) > 1

    @pytest.mark.asyncio
    async def test_generate_report_with_detailed_data(
        self, mock_context, sample_pricing_data_web, temp_output_dir
    ):
        """Test generating a report with detailed cost data."""
        detailed_cost_data = {
            'services': {
                'AWS Lambda': {
                    'usage': '1M requests per month',
                    'estimated_cost': '$20.00',
                    'unit_pricing': {
                        'requests': '$0.20 per 1M requests',
                        'compute': '$0.0000166667 per GB-second',
                    },
                }
            }
        }

        result = await generate_cost_report_wrapper(
            mock_context,
            pricing_data=sample_pricing_data_web,
            service_name='AWS Lambda',
            detailed_cost_data=detailed_cost_data,
            output_file=f'{temp_output_dir}/report.md',
            pricing_model='ON DEMAND',
            related_services=None,
            assumptions=None,
            exclusions=None,
            recommendations=None,
        )

        assert result is not None
        assert isinstance(result, str)
        assert 'AWS Lambda' in result
        assert '$20.00' in result
        assert '1M requests per month' in result

    @pytest.mark.asyncio
    async def test_generate_report_error_handling(self, mock_context):
        """Test error handling in report generation."""
        result = await generate_cost_report_wrapper(
            mock_context,
            pricing_data={'status': 'error'},
            service_name='Invalid Service',
            pricing_model='ON DEMAND',
            related_services=None,
            assumptions=None,
            exclusions=None,
            output_file=None,
            detailed_cost_data=None,
            recommendations=None,
        )

        assert '# Invalid Service Cost Analysis' in result


class TestGetPricingServiceAttributes:
    """Tests for the get_pricing_service_attributes function."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'service_code,product_attrs,expected',
        [
            (
                'AmazonEC2',
                {'instanceType': 'x', 'location': 'y', 'tenancy': 'z', 'operatingSystem': 'w'},
                ['instanceType', 'location', 'operatingSystem', 'tenancy'],
            ),
            (
                'AmazonRDS',
                {'engineCode': 'x', 'instanceType': 'y', 'location': 'z', 'databaseEngine': 'w'},
                ['databaseEngine', 'engineCode', 'instanceType', 'location'],
            ),
        ],
    )
    async def test_get_pricing_service_attributes(
        self, mock_context, service_code, product_attrs, expected
    ):
        """Test getting service attributes for various AWS services."""
        bulk_data = {
            'products': {
                'SKU001': {
                    'sku': 'SKU001',
                    'productFamily': 'Compute',
                    'attributes': product_attrs,
                },
            },
            'terms': {},
        }
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing_service_attributes(mock_context, service_code)

            assert result == expected
            mock_context.info.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'attributes,filter_pattern,expected_matches,expected_count,test_description',
        [
            (
                {'instanceType': 'x', 'instanceFamily': 'y', 'location': 'z', 'memory': 'w', 'vcpu': 'v'},
                'instance',
                ['instanceFamily', 'instanceType'],
                None,
                'basic_instance_filter',
            ),
            (
                {'instanceType': 'x', 'location': 'y', 'tenancy': 'z'},
                None,
                None,
                3,
                'no_filter_all_attributes',
            ),
            (
                {'instanceType': 'x', 'location': 'y', 'tenancy': 'z'},
                '',
                None,
                3,
                'empty_filter_all_attributes',
            ),
            (
                {'storageClass': 'x', 'location': 'y'},
                'Storage',
                ['storageClass'],
                None,
                'case_insensitive_partial_match',
            ),
        ],
    )
    async def test_get_pricing_service_attributes_filtering_happy_path(
        self,
        mock_context,
        attributes,
        filter_pattern,
        expected_matches,
        expected_count,
        test_description,
    ):
        """Test successful filtering of service attributes with various patterns."""
        bulk_data = {
            'products': {
                'SKU001': {'sku': 'SKU001', 'productFamily': 'Compute', 'attributes': attributes},
            },
            'terms': {},
        }
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing_service_attributes(
                mock_context, 'AmazonEC2', filter=filter_pattern
            )

            assert isinstance(result, list), (
                f'Failed {test_description}: expected list, got {type(result)}'
            )

            if expected_matches is not None:
                assert len(result) == len(expected_matches), (
                    f'Failed {test_description}: expected {len(expected_matches)} matches, got {len(result)}'
                )
                for attr in expected_matches:
                    assert attr in result, f'Failed {test_description}: missing {attr} in results'
                assert result == sorted(result)
            elif expected_count is not None:
                assert len(result) == expected_count, (
                    f'Failed {test_description}: expected {expected_count} attributes, got {len(result)}'
                )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'attributes,filter_pattern,expected_error_type,test_description',
        [
            (
                {'instanceType': 'x', 'location': 'y', 'tenancy': 'z'},
                'nonexistent',
                'no_matches_found',
                'filter_no_matches',
            ),
            (
                {'engineCode': 'x', 'instanceType': 'y', 'location': 'z'},
                '[invalid',
                'invalid_regex',
                'invalid_regex_pattern',
            ),
        ],
    )
    async def test_get_pricing_service_attributes_filtering_errors(
        self,
        mock_context,
        attributes,
        filter_pattern,
        expected_error_type,
        test_description,
    ):
        """Test error scenarios in service attributes filtering."""
        bulk_data = {
            'products': {
                'SKU001': {'sku': 'SKU001', 'productFamily': 'Compute', 'attributes': attributes},
            },
            'terms': {},
        }
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing_service_attributes(
                mock_context, 'AmazonEC2', filter=filter_pattern
            )

            assert isinstance(result, dict)
            assert result['status'] == 'error'
            assert result['error_type'] == expected_error_type

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'error_scenario,expected_error_type,expected_in_message',
        [
            ('service_not_found', 'service_not_found', 'was not found'),
            ('api_error', 'api_error', 'API Error'),
            ('empty_attributes', 'empty_results', 'no filterable attributes available'),
        ],
    )
    async def test_get_pricing_service_attributes_errors(
        self,
        mock_context,
        error_scenario,
        expected_error_type,
        expected_in_message,
    ):
        """Test various error scenarios for get_pricing_service_attributes."""
        if error_scenario == 'service_not_found':
            mock_side_effect = Exception('404 Not Found')
        elif error_scenario == 'api_error':
            mock_side_effect = Exception('API Error')
        elif error_scenario == 'empty_attributes':
            mock_side_effect = None  # Will return empty products

        if error_scenario == 'empty_attributes':
            empty_data = {'products': {'SKU001': {'sku': 'SKU001', 'attributes': {}}}, 'terms': {}}
            with patch(
                'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
                new_callable=AsyncMock,
                return_value=empty_data,
            ):
                result = await get_pricing_service_attributes(mock_context, 'TestService')
        else:
            with patch(
                'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
                new_callable=AsyncMock,
                side_effect=mock_side_effect,
            ):
                service_code = 'InvalidService' if error_scenario == 'service_not_found' else 'AmazonEC2'
                result = await get_pricing_service_attributes(mock_context, service_code)

        assert isinstance(result, dict)
        assert result['status'] == 'error'
        assert result['error_type'] == expected_error_type
        assert expected_in_message in result['message']
        mock_context.error.assert_called()

    @pytest.mark.asyncio
    async def test_get_pricing_service_attributes_fetch_error(self, mock_context):
        """Test handling of fetch errors."""
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            side_effect=Exception('Connection failed'),
        ):
            result = await get_pricing_service_attributes(mock_context, 'AmazonEC2')

        assert isinstance(result, dict)
        assert result['status'] == 'error'
        assert result['error_type'] == 'api_error'
        assert 'Connection failed' in result['message']
        mock_context.error.assert_called()


class TestGetPricingAttributeValues:
    """Tests for the get_pricing_attribute_values function."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'service_code,attribute_names,product_attrs_list,filters,expected,test_description',
        [
            (
                'AmazonEC2',
                ['instanceType'],
                [
                    {'instanceType': 't2.micro'},
                    {'instanceType': 't2.small'},
                    {'instanceType': 't3.medium'},
                    {'instanceType': 'm5.large'},
                ],
                None,
                {'instanceType': ['m5.large', 't2.micro', 't2.small', 't3.medium']},
                'single_attribute_no_filter',
            ),
            (
                'AmazonEC2',
                ['instanceType', 'location'],
                [
                    {'instanceType': 't2.micro', 'location': 'US East (N. Virginia)'},
                    {'instanceType': 't2.small', 'location': 'US West (Oregon)'},
                    {'instanceType': 't3.medium', 'location': 'EU (Ireland)'},
                ],
                None,
                {
                    'instanceType': ['t2.micro', 't2.small', 't3.medium'],
                    'location': ['EU (Ireland)', 'US East (N. Virginia)', 'US West (Oregon)'],
                },
                'multiple_attributes_no_filter',
            ),
            (
                'AmazonEC2',
                ['instanceType', 'location'],
                [
                    {'instanceType': 't2.micro', 'location': 'US East (N. Virginia)'},
                    {'instanceType': 't2.small', 'location': 'US West (Oregon)'},
                    {'instanceType': 't3.medium', 'location': 'EU (Ireland)'},
                    {'instanceType': 'm5.large', 'location': 'US East (N. Virginia)'},
                ],
                {'instanceType': 't3'},
                {
                    'instanceType': ['t3.medium'],
                    'location': ['EU (Ireland)', 'US East (N. Virginia)', 'US West (Oregon)'],
                },
                'partial_filtering',
            ),
            (
                'AmazonEC2',
                ['instanceType'],
                [
                    {'instanceType': 't2.micro'},
                    {'instanceType': 't3.medium'},
                ],
                {},
                {'instanceType': ['t2.micro', 't3.medium']},
                'empty_filters_dict',
            ),
            (
                'AmazonEC2',
                ['location'],
                [
                    {'location': 'US East (N. Virginia)'},
                    {'location': 'US West (Oregon)'},
                    {'location': 'EU (Ireland)'},
                ],
                {'location': 'us'},
                {'location': ['US East (N. Virginia)', 'US West (Oregon)']},
                'case_insensitive_filtering',
            ),
            (
                'AmazonEC2',
                ['instanceType'],
                [
                    {'instanceType': 't2.micro'},
                    {'instanceType': 't3.medium'},
                ],
                {'instanceType': 't3', 'nonRequestedAttribute': 'someFilter'},
                {'instanceType': ['t3.medium']},
                'ignore_non_requested_attribute_filter',
            ),
            (
                'AmazonEC2',
                ['instanceType'],
                [
                    {'instanceType': 't2.micro'},
                    {'instanceType': 't3.medium'},
                ],
                {'instanceType': 'nonexistent'},
                {'instanceType': []},
                'filter_no_matches',
            ),
        ],
    )
    async def test_get_pricing_attribute_values_happy_path(
        self,
        mock_context,
        service_code,
        attribute_names,
        product_attrs_list,
        filters,
        expected,
        test_description,
    ):
        """Test successful cases for getting attribute values with and without filtering."""
        products = {}
        for i, attrs in enumerate(product_attrs_list):
            sku = f'SKU{i:03d}'
            products[sku] = {
                'sku': sku,
                'productFamily': 'Compute',
                'attributes': attrs,
            }
        bulk_data = {'products': products, 'terms': {}}

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing_attribute_values(
                mock_context, service_code, None, attribute_names, filters
            )

            assert result == expected, f"Failed test case '{test_description}'"
            mock_context.info.assert_called()

    @pytest.mark.asyncio
    async def test_get_pricing_attribute_values_filter_invalid_regex(self, mock_context):
        """Test error handling when invalid regex pattern is provided."""
        products = {
            'SKU001': {'sku': 'SKU001', 'attributes': {'instanceType': 't2.micro'}},
        }
        bulk_data = {'products': products, 'terms': {}}

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing_attribute_values(
                mock_context, 'AmazonEC2', None, ['instanceType'], {'instanceType': '[invalid'}
            )

            assert isinstance(result, dict)
            assert result['status'] == 'error'
            assert result['error_type'] == 'invalid_regex'
            assert 'Invalid regex pattern "[invalid"' in result['message']
            assert result['service_code'] == 'AmazonEC2'
            assert result['attribute_name'] == 'instanceType'
            mock_context.error.assert_called()

    @pytest.mark.asyncio
    async def test_get_pricing_attribute_values_empty_attribute_list(self, mock_context):
        """Test error handling when empty attribute list is provided."""
        result = await get_pricing_attribute_values(mock_context, 'AmazonEC2', None, [])

        assert isinstance(result, dict)
        assert result['status'] == 'error'
        assert result['error_type'] == 'empty_attribute_list'
        assert 'No attribute names provided' in result['message']
        assert result['service_code'] == 'AmazonEC2'
        assert result['attribute_names'] == []
        assert 'get_pricing_service_attributes()' in result['suggestion']
        mock_context.error.assert_called()

    @pytest.mark.asyncio
    async def test_get_pricing_attribute_values_single_attribute_empty(self, mock_context):
        """Test getting attribute values when no values are returned for single attribute."""
        products = {
            'SKU001': {'sku': 'SKU001', 'attributes': {'otherAttr': 'value'}},
        }
        bulk_data = {'products': products, 'terms': {}}

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing_attribute_values(
                mock_context, 'InvalidService', None, ['invalidAttribute']
            )

            assert isinstance(result, dict)
            assert result['status'] == 'error'
            assert result['error_type'] == 'no_attribute_values_found'
            assert 'InvalidService' in result['message']
            assert 'invalidAttribute' in result['message']
            mock_context.error.assert_called()

    @pytest.mark.asyncio
    async def test_get_pricing_attribute_values_all_or_nothing_failure(self, mock_context):
        """Test all-or-nothing behavior when one attribute fails in multi-attribute request."""
        products = {
            'SKU001': {'sku': 'SKU001', 'attributes': {'instanceType': 't2.micro'}},
        }
        bulk_data = {'products': products, 'terms': {}}

        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            result = await get_pricing_attribute_values(
                mock_context, 'AmazonEC2', None, ['instanceType', 'invalidAttribute']
            )

            assert isinstance(result, dict)
            assert result['status'] == 'error'
            assert result['error_type'] == 'no_attribute_values_found'
            assert (
                'Failed to retrieve values for attribute "invalidAttribute"' in result['message']
            )
            assert result['failed_attribute'] == 'invalidAttribute'
            assert result['requested_attributes'] == ['instanceType', 'invalidAttribute']

    @pytest.mark.asyncio
    async def test_get_pricing_attribute_values_fetch_error(self, mock_context):
        """Test handling of fetch errors."""
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            side_effect=Exception('Connection failed'),
        ):
            result = await get_pricing_attribute_values(
                mock_context, 'AmazonEC2', None, ['instanceType']
            )

        assert isinstance(result, dict)
        assert result['status'] == 'error'
        assert result['error_type'] == 'api_error'
        assert 'Connection failed' in result['message']
        mock_context.error.assert_called()


class TestGetPricingServiceCodesFiltering:
    """Tests for regex filtering functionality in get_pricing_service_codes."""

    @pytest.fixture
    def mock_service_index(self):
        """Mock service index response with a variety of AWS services."""
        return {
            'offers': {
                'AmazonBedrock': {'offerCode': 'AmazonBedrock'},
                'AmazonBedrockService': {'offerCode': 'AmazonBedrockService'},
                'AmazonEC2': {'offerCode': 'AmazonEC2'},
                'AmazonS3': {'offerCode': 'AmazonS3'},
                'AmazonRDS': {'offerCode': 'AmazonRDS'},
                'AWSLambda': {'offerCode': 'AWSLambda'},
                'AmazonDynamoDB': {'offerCode': 'AmazonDynamoDB'},
                'AmazonElasticSearch': {'offerCode': 'AmazonElasticSearch'},
                'AmazonKendra': {'offerCode': 'AmazonKendra'},
                'AmazonSageMaker': {'offerCode': 'AmazonSageMaker'},
            }
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'filter_pattern,expected_matches,expected_count,test_description',
        [
            ('bedrock', ['AmazonBedrock', 'AmazonBedrockService'], None, 'basic_case_insensitive'),
            (
                'BEDROCK',
                ['AmazonBedrock', 'AmazonBedrockService'],
                None,
                'uppercase_case_insensitive',
            ),
            ('^AmazonBedrock$', ['AmazonBedrock'], None, 'exact_match_regex'),
            ('Lambda|S3', ['AWSLambda', 'AmazonS3'], None, 'alternation_regex'),
            ('Amazon.*DB', ['AmazonDynamoDB'], None, 'wildcard_regex'),
            ('EC2', ['AmazonEC2'], None, 'simple_substring'),
            ('AWS', ['AWSLambda'], None, 'aws_prefix'),
            ('Search', ['AmazonElasticSearch'], None, 'partial_match'),
            ('', None, 10, 'empty_filter_all_services'),
            (None, None, 10, 'none_filter_all_services'),
        ],
    )
    async def test_regex_filtering_happy_path(
        self,
        mock_context,
        mock_service_index,
        filter_pattern,
        expected_matches,
        expected_count,
        test_description,
    ):
        """Test successful regex filter patterns and no-filter cases."""
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_service_index',
            new_callable=AsyncMock,
            return_value=mock_service_index,
        ):
            result = await get_pricing_service_codes(mock_context, filter=filter_pattern)

            assert isinstance(result, list), (
                f'Failed {test_description}: expected list, got {type(result)}'
            )

            if expected_matches is not None:
                assert len(result) == len(expected_matches), (
                    f'Failed {test_description}: expected {len(expected_matches)} matches, got {len(result)}'
                )
                for service in expected_matches:
                    assert service in result, (
                        f'Failed {test_description}: missing {service} in results'
                    )
            elif expected_count is not None:
                assert len(result) == expected_count, (
                    f'Failed {test_description}: expected {expected_count} services, got {len(result)}'
                )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'filter_pattern,expected_error_type,test_description',
        [
            (r'\bEC2\b', 'no_matches_found', 'word_boundary_no_matches'),
            (r'\.', 'no_matches_found', 'literal_dot_no_matches'),
            ('NonExistentService', 'no_matches_found', 'nonexistent_service'),
            ('[invalid', 'invalid_regex', 'invalid_regex_pattern'),
        ],
    )
    async def test_regex_filtering_error_cases(
        self,
        mock_context,
        mock_service_index,
        filter_pattern,
        expected_error_type,
        test_description,
    ):
        """Test regex filter patterns that result in errors."""
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_service_index',
            new_callable=AsyncMock,
            return_value=mock_service_index,
        ):
            result = await get_pricing_service_codes(mock_context, filter=filter_pattern)

            assert isinstance(result, dict), (
                f'Failed {test_description}: expected dict (error), got {type(result)}'
            )
            assert result['status'] == 'error'
            assert result['error_type'] == expected_error_type
            mock_context.error.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'error_scenario,expected_error_type',
        [
            ('api_error', 'api_error'),
        ],
    )
    async def test_filter_error_scenarios(
        self, mock_context, error_scenario, expected_error_type
    ):
        """Test error handling scenarios with filtering enabled."""
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_service_index',
            new_callable=AsyncMock,
            side_effect=Exception('API Error'),
        ):
            result = await get_pricing_service_codes(mock_context, filter='bedrock')

        assert isinstance(result, dict)
        assert result['status'] == 'error'
        assert result['error_type'] == expected_error_type
        mock_context.error.assert_called()


class TestServerIntegration:
    """Integration tests for the server module."""

    @pytest.mark.asyncio
    async def test_get_pricing_service_codes_integration(self, mock_context, sample_service_index):
        """Test the get_pricing_service_codes tool returns well-known service codes."""
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_service_index',
            new_callable=AsyncMock,
            return_value=sample_service_index,
        ):
            service_codes = await get_pricing_service_codes(mock_context, filter=None)

            assert service_codes is not None
            assert isinstance(service_codes, list)

            expected_codes = ['AmazonEC2', 'AmazonS3', 'AmazonRDS', 'AWSLambda', 'AmazonDynamoDB']

            for code in expected_codes:
                assert code in service_codes, f'Expected service code {code} not found in response'

            mock_context.info.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'error_scenario,expected_error_type',
        [
            ('api_error', 'api_error'),
            ('empty_results', 'empty_results'),
        ],
    )
    async def test_get_pricing_service_codes_errors(
        self, mock_context, error_scenario, expected_error_type
    ):
        """Test error handling scenarios for get_pricing_service_codes."""
        if error_scenario == 'api_error':
            with patch(
                'awslabs.aws_pricing_mcp_server.server.fetch_service_index',
                new_callable=AsyncMock,
                side_effect=Exception('API Error'),
            ):
                result = await get_pricing_service_codes(mock_context)
        elif error_scenario == 'empty_results':
            with patch(
                'awslabs.aws_pricing_mcp_server.server.fetch_service_index',
                new_callable=AsyncMock,
                return_value={'offers': {}},
            ):
                result = await get_pricing_service_codes(mock_context)

        assert isinstance(result, dict)
        assert result['status'] == 'error'
        assert result['error_type'] == expected_error_type
        mock_context.error.assert_called()

    @pytest.mark.asyncio
    async def test_pricing_workflow(self, mock_context):
        """Test the complete pricing analysis workflow."""
        bulk_data = _make_bulk_response([
            {
                'sku': 'SKU001',
                'productFamily': 'Serverless',
                'attributes': {
                    'productFamily': 'Serverless',
                    'description': 'Run code without thinking about servers',
                },
                'pricePerUnit': {'USD': '0.20'},
                'unit': 'requests',
            }
        ])
        with patch(
            'awslabs.aws_pricing_mcp_server.server.fetch_price_list',
            new_callable=AsyncMock,
            return_value=bulk_data,
        ):
            api_pricing = await get_pricing(mock_context, 'AWSLambda', 'us-west-2')
        assert api_pricing is not None
        assert api_pricing['status'] == 'success'

        report = await generate_cost_report_wrapper(
            mock_context,
            pricing_data=api_pricing,
            service_name='AWS Lambda',
            pricing_model='ON DEMAND',
            related_services=None,
            assumptions=None,
            exclusions=None,
            output_file=None,
            detailed_cost_data=None,
            recommendations=None,
        )
        assert report is not None
        assert isinstance(report, str)
        assert 'AWS Lambda' in report


class TestIsFreeProduct:
    """Tests for the _is_free_product function with multi-currency support."""

    def _create_pricing_data(self, price_per_unit: dict) -> dict:
        """Helper to create test pricing data structure."""
        return {
            'terms': {
                'OnDemand': {
                    'TEST.TERM.CODE': {
                        'priceDimensions': {'TEST.TERM.CODE.DIM': {'pricePerUnit': price_per_unit}}
                    }
                }
            }
        }

    @pytest.mark.parametrize(
        'price_per_unit,expected_result,test_description',
        [
            ({'USD': '0.0000', 'CNY': '0.0000'}, True, 'truly_free_all_zero'),
            ({'CNY': '5.2000'}, False, 'cny_only_paid'),
            ({'USD': '0.0000', 'CNY': '3.5000'}, False, 'usd_free_cny_paid'),
            ({'CNY': 'N/A'}, False, 'invalid_cny_format'),
            (
                {'USD': '0.0000', 'EUR': '0.0000', 'CNY': '8.7500'},
                False,
                'multi_currency_cny_paid',
            ),
        ],
    )
    def test_is_free_product_multi_currency(
        self, price_per_unit, expected_result, test_description
    ):
        """Test _is_free_product correctly handles CNY and other currencies."""
        pricing_data = self._create_pricing_data(price_per_unit)
        result = _is_free_product(pricing_data)

        assert result == expected_result, (
            f"Failed test case '{test_description}': "
            f'Expected {expected_result}, got {result} for pricing {price_per_unit}'
        )


class TestGetPriceListUrls:
    """Tests for the get_price_list_urls function."""

    @pytest.mark.asyncio
    async def test_get_price_list_urls_success(self, mock_context):
        """Test successful retrieval of price list file URLs."""
        result = await get_price_list_urls(mock_context, 'AmazonEC2', 'us-east-1')

        assert len(result) == 2
        assert 'csv' in result
        assert 'json' in result
        assert 'AmazonEC2' in result['csv']
        assert 'us-east-1' in result['csv']
        assert result['csv'].endswith('.csv')
        assert 'AmazonEC2' in result['json']
        assert 'us-east-1' in result['json']
        assert result['json'].endswith('.json')

    @pytest.mark.asyncio
    async def test_get_price_list_urls_different_region(self, mock_context):
        """Test URLs are correctly constructed for different regions."""
        result = await get_price_list_urls(mock_context, 'AmazonS3', 'eu-west-1')

        assert 'AmazonS3' in result['csv']
        assert 'eu-west-1' in result['csv']
        assert 'AmazonS3' in result['json']
        assert 'eu-west-1' in result['json']

    @pytest.mark.asyncio
    async def test_get_price_list_urls_url_format(self, mock_context):
        """Test the exact URL format matches the Bulk API pattern."""
        result = await get_price_list_urls(mock_context, 'AmazonEC2', 'us-east-1')

        expected_base = 'https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws'
        assert result['csv'] == f'{expected_base}/AmazonEC2/current/us-east-1/index.csv'
        assert result['json'] == f'{expected_base}/AmazonEC2/current/us-east-1/index.json'
