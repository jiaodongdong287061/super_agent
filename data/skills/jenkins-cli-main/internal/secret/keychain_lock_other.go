//go:build !darwin

package secret

func withKeychainLock(fn func() error) error {
	return fn()
}
