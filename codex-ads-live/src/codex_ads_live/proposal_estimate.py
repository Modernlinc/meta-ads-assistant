"""Use exact observed rates for a local budget estimate before display rounding."""
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext
from fractions import Fraction


def display_ratio(ratio, rounding=ROUND_HALF_EVEN):
    with localcontext() as arithmetic:
        arithmetic.prec = 64
        arithmetic.rounding = rounding
        return format(Decimal(ratio.numerator)/ratio.denominator,'.4f')


def estimate_test(spend, count, target, cap, minimum):
    observed_cost = Fraction(Decimal(spend))/Fraction(count)
    target_cost = Fraction(Decimal(target))
    target_check = ('ABOVE_TARGET' if observed_cost>target_cost else
                    'AT_TARGET' if observed_cost==target_cost else 'BELOW_TARGET')
    result = dict(target_check=target_check,estimated_test_capacity=None)
    if cap is None:
        return dict(result,capacity_check='TEST_CAP_NOT_DECLARED')
    if Decimal(cap)==0:
        return dict(result,capacity_check='NO_TEST_SPEND_DECLARED')
    planning_cost = max(observed_cost,target_cost)
    estimated_count = Fraction(Decimal(cap))/planning_cost
    return dict(result,capacity_check=('SUPPORTS_DECLARED_SAMPLE_ESTIMATE' if estimated_count>=minimum
                                      else 'BELOW_DECLARED_SAMPLE_ESTIMATE'),
                estimated_test_capacity=dict(
                    cost_basis='HIGHER_OF_OBSERVED_AND_TARGET_COST',cost_per_result=display_ratio(planning_cost),
                    outcomes_at_test_cap=display_ratio(estimated_count,ROUND_DOWN),
                    spend_for_declared_minimum=display_ratio(planning_cost*minimum,ROUND_UP)))
