//go:build !faults

package main

// The production build: no fault code at all. fault() is a constant false the compiler removes.
const faultsBuilt = false

func fault(string) bool { return false }

func setFaults(...string) {}
