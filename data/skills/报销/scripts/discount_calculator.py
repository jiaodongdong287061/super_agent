#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
飞机票折扣计算脚本
剔除机建费和燃油费后，根据最贵全价机票计算折扣率
"""

import argparse
import json
import sys


def calculate_discount(actual_fare: float, 
                       construction_fee: float, 
                       fuel_surcharge: float, 
                       full_price: float,
                       airline: str = "") -> dict:
    """
    计算机票折扣率
    
    参数:
        actual_fare: 实际支付票价（含机建燃油）
        construction_fee: 机场建设费
        fuel_surcharge: 燃油附加费
        full_price: 全价机票价格
        airline: 航空公司名称（可选）
    
    返回:
        包含折扣信息的字典
    """
    # 剔除机建费和燃油费，得到实际票面价
    ticket_price = actual_fare - construction_fee - fuel_surcharge
    
    # 计算折扣率
    if full_price <= 0:
        return {
            "error": "全价必须大于0",
            "ticket_price": ticket_price,
            "discount_rate": None
        }
    
    discount_rate = ticket_price / full_price
    
    # 转换为折扣表示（如 0.69 -> 6.9折）
    discount_display = round(discount_rate * 10, 1)
    
    return {
        "actual_fare": actual_fare,
        "construction_fee": construction_fee,
        "fuel_surcharge": fuel_surcharge,
        "ticket_price": round(ticket_price, 2),
        "full_price": full_price,
        "discount_rate": round(discount_rate, 4),
        "discount_display": f"{discount_display}折",
        "airline": airline
    }


def find_best_full_price(full_prices: list) -> dict:
    """
    从多个航司的全价中找出最贵的作为基准
    
    参数:
        full_prices: 航司全价列表，格式: [{"airline": "国航", "price": 1500}, ...]
    
    返回:
        最贵全价信息
    """
    if not full_prices:
        return {"error": "全价列表为空"}
    
    best = max(full_prices, key=lambda x: x.get("price", 0))
    return {
        "base_airline": best.get("airline", "未知"),
        "base_full_price": best.get("price", 0)
    }


def main():
    parser = argparse.ArgumentParser(description="计算飞机票折扣率")
    parser.add_argument("--actual", type=float, required=True, help="实际支付票价")
    parser.add_argument("--construction", type=float, default=50, help="机场建设费（默认50元）")
    parser.add_argument("--fuel", type=float, default=120, help="燃油附加费（默认120元，800km以下60元）")
    parser.add_argument("--full", type=float, required=True, help="全价机票价格")
    parser.add_argument("--airline", type=str, default="", help="航空公司名称")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")
    
    args = parser.parse_args()
    
    result = calculate_discount(
        actual_fare=args.actual,
        construction_fee=args.construction,
        fuel_surcharge=args.fuel,
        full_price=args.full,
        airline=args.airline
    )
    
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if "error" in result:
            print(f"错误: {result['error']}")
            sys.exit(1)
        
        print(f"实际支付: {result['actual_fare']}元")
        print(f"机建费: {result['construction_fee']}元")
        print(f"燃油费: {result['fuel_surcharge']}元")
        print(f"票面价: {result['ticket_price']}元")
        print(f"全价基准: {result['full_price']}元")
        print(f"折扣率: {result['discount_display']}")


if __name__ == "__main__":
    main()
